"""Simulation workflow built around ETC apply/delete operations."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
import yaml

from .kubectl import CommandError, run_command
from .specs import (
    SIMNODE_ALLOWED_FIELDS,
    AttachmentSpec,
    SimNodeSpec,
    SimulationSpec,
    SpecError,
    parse_simulation_spec,
    read_yaml,
)


@dataclass(frozen=True)
class ResourceSummary:
    """Light-weight reference to a rendered resource."""

    kind: str
    name: str


@dataclass(frozen=True)
class NodeInterfaceConfig:
    """Information required to configure VLAN/IP inside a pod."""

    name: str
    interface: str
    ip_address: str
    vlan: str | None


@dataclass(frozen=True)
class RenderedBundle:
    """Rendered simulation manifests alongside operational metadata."""

    manifest_text: str
    summaries: list[ResourceSummary]
    node_configs: list[NodeInterfaceConfig]
    sim_nodes: list[str]


def _slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "sim"


def _string_map(payload: dict[str, object] | None) -> dict[str, str]:
    if not payload:
        return {}
    return {
        str(key): str(value)
        for key, value in payload.items()
        if isinstance(key, str) and value is not None
    }


def _attachments_by_node(attachments: Iterable[AttachmentSpec]) -> dict[str, list[AttachmentSpec]]:
    grouped: dict[str, list[AttachmentSpec]] = {}
    for attachment in attachments:
        grouped.setdefault(attachment.sim_node, []).append(attachment)
    return grouped


def _select_vlan(node: SimNodeSpec, attachments: list[AttachmentSpec]) -> str | None:
    if node.vlan:
        return node.vlan
    for attachment in attachments:
        if attachment.vlan:
            return attachment.vlan
    return None


def _select_interface(node: SimNodeSpec, attachments: list[AttachmentSpec]) -> str | None:
    if node.interface:
        return node.interface
    for attachment in attachments:
        if attachment.sim_interface:
            return attachment.sim_interface
    return None


def _render_simnode(
    *,
    node: SimNodeSpec,
    namespace: str,
    attachments: list[AttachmentSpec],
) -> tuple[dict[str, object], NodeInterfaceConfig | None]:
    vlan = _select_vlan(node, attachments)
    interface = _select_interface(node, attachments)

    metadata = {
        "name": node.name,
        "namespace": namespace,
        "labels": {
            "eda.nokia.com/simtopology": "true",
        },
    }

    node_labels = node.raw.get("labels")
    if isinstance(node_labels, dict):
        metadata.setdefault("labels", {}).update(_string_map(node_labels))

    annotations = node.raw.get("annotations")
    if isinstance(annotations, dict):
        metadata["annotations"] = _string_map(annotations)

    spec: dict[str, object] = {
        "containerImage": node.image,
        "operatingSystem": node.node_type.lower(),
    }

    defaults: dict[str, object] = {
        "dhcp": {},
    }
    if node.node_type.lower() == "linux":
        defaults.setdefault("port", 57400)
        defaults.setdefault("serialNumberPath", "")
        defaults.setdefault("versionPath", "")
    for key, value in defaults.items():
        spec.setdefault(key, value)

    allowed_top_level = {
        key: node.raw[key]
        for key in SIMNODE_ALLOWED_FIELDS
        if key in node.raw and node.raw[key] is not None
    }
    spec.update(allowed_top_level)
    if node.spec_overrides:
        spec.update(dict(node.spec_overrides))

    # Ensure canonical fields keep their expected value.
    spec["containerImage"] = node.image
    spec["operatingSystem"] = node.node_type.lower()

    manifest = {
        "apiVersion": "core.eda.nokia.com/v1",
        "kind": "SimNode",
        "metadata": metadata,
        "spec": spec,
    }

    node_config: NodeInterfaceConfig | None = None
    if interface and node.ip_address:
        node_config = NodeInterfaceConfig(
            name=node.name,
            interface=interface,
            ip_address=node.ip_address,
            vlan=vlan,
        )

    return manifest, node_config


def _render_simlink(
    *,
    attachment: AttachmentSpec,
    namespace: str,
) -> dict[str, object]:
    link_name = "-".join(
        (
            _slugify(attachment.fabric_node),
            _slugify(attachment.fabric_interface),
            _slugify(attachment.sim_node),
        )
    )

    spec_link = {
        "local": {
            "node": attachment.fabric_node,
            "interface": attachment.fabric_interface,
            "interfaceResource": f"{_slugify(attachment.fabric_node)}-{_slugify(attachment.fabric_interface)}",
        },
        "sim": {
            "node": attachment.sim_node,
            "interface": attachment.sim_interface,
            "interfaceResource": f"{_slugify(attachment.sim_node)}-{_slugify(attachment.sim_interface)}",
        },
    }

    return {
        "apiVersion": "core.eda.nokia.com/v1",
        "kind": "SimLink",
        "metadata": {
            "name": link_name,
            "namespace": namespace,
            "labels": {
                "eda.nokia.com/simtopology": "true",
            },
        },
        "spec": {
            "links": [spec_link],
        },
    }


def _render_topolink(*, attachment: AttachmentSpec, namespace: str) -> dict[str, object]:
    link_name = "-".join(
        (
            _slugify(attachment.fabric_node),
            _slugify(attachment.fabric_interface),
            _slugify(attachment.sim_node),
        )
    )

    local_interface_resource = f"{_slugify(attachment.fabric_node)}-{_slugify(attachment.fabric_interface)}"
    remote_interface_resource = f"{_slugify(attachment.sim_node)}-{_slugify(attachment.sim_interface)}"

    return {
        "apiVersion": "core.eda.nokia.com/v1",
        "kind": "TopoLink",
        "metadata": {
            "name": link_name,
            "namespace": namespace,
            "labels": {
                "eda.nokia.com/role": "edge",
                "eda.nokia.com/simtopology": "true",
            },
        },
        "spec": {
            "links": [
                {
                    "type": "edge",
                    "local": {
                        "node": attachment.fabric_node,
                        "interface": attachment.fabric_interface,
                        "interfaceResource": local_interface_resource,
                    },
                    "remote": {
                        "node": attachment.sim_node,
                        "interface": attachment.sim_interface,
                        "interfaceResource": remote_interface_resource,
                    },
                }
            ],
        },
    }


def _render_bundle(spec: SimulationSpec, *, namespace: str) -> RenderedBundle:
    attachments_grouped = _attachments_by_node(spec.attachments)
    documents: list[str] = []
    summaries: list[ResourceSummary] = []
    node_configs: list[NodeInterfaceConfig] = []

    for node in spec.ordered_nodes:
        attachments = attachments_grouped.get(node.name, [])
        manifest, config = _render_simnode(
            node=node,
            namespace=namespace,
            attachments=attachments,
        )
        documents.append(yaml.safe_dump(manifest, sort_keys=False).rstrip())
        summaries.append(ResourceSummary(kind="SimNode", name=node.name))
        if config is not None:
            node_configs.append(config)

    for attachment in spec.attachments:
        simlink_manifest = _render_simlink(
            attachment=attachment,
            namespace=namespace,
        )
        documents.append(yaml.safe_dump(simlink_manifest, sort_keys=False).rstrip())
        summaries.append(
            ResourceSummary(kind="SimLink", name=simlink_manifest["metadata"]["name"])
        )

    for attachment in spec.attachments:
        topolink_manifest = _render_topolink(attachment=attachment, namespace=namespace)
        documents.append(yaml.safe_dump(topolink_manifest, sort_keys=False).rstrip())
        summaries.append(
            ResourceSummary(kind="TopoLink", name=topolink_manifest["metadata"]["name"])
        )

    manifest_text = "\n---\n".join(documents) + "\n"
    sim_nodes = [node.name for node in spec.ordered_nodes]
    return RenderedBundle(
        manifest_text=manifest_text,
        summaries=summaries,
        node_configs=node_configs,
        sim_nodes=sim_nodes,
    )


def _write_manifest(text: str, *, target: Path | None) -> Path:
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", prefix="cx-attach-", delete=False) as handle:
        handle.write(text)
        return Path(handle.name)


def _group_summaries(summaries: Iterable[ResourceSummary]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for summary in summaries:
        grouped.setdefault(summary.kind, []).append(summary.name)
    return grouped


def _run_etc(command: Iterable[str], *, debug: bool) -> str:
    output = run_command(command)
    if debug:
        typer.echo("Debug: command output\n" + (output or "<no output>"))
    elif output:
        typer.echo(output)
    return output


def _wait_for_sim_pod(core_ns: str, sim_node: str, *, debug: bool) -> str:
    selector = f"cx-pod-name={sim_node}"
    deadline = time.time() + 180
    pod_name = ""
    while time.time() < deadline:
        try:
            pod_name = run_command(
                (
                    "kubectl",
                    "-n",
                    core_ns,
                    "get",
                    "pod",
                    "-l",
                    selector,
                    "-o",
                    "jsonpath={.items[0].metadata.name}",
                )
            )
        except CommandError:
            pod_name = ""

        if pod_name:
            try:
                run_command(
                    (
                        "kubectl",
                        "-n",
                        core_ns,
                        "wait",
                        "pod",
                        pod_name,
                        "--for=condition=Ready",
                        "--timeout=120s",
                    )
                )
            except CommandError:
                pod_name = ""
                time.sleep(2)
                continue
            if debug:
                typer.echo(f"Debug: pod ready for {sim_node}: {pod_name}")
            return pod_name
        time.sleep(2)

    raise RuntimeError(f"Timed out waiting for pod backing sim node {sim_node}")


def _collect_sim_pods(core_ns: str, sim_nodes: Iterable[str], *, debug: bool) -> dict[str, str]:
    pods: dict[str, str] = {}
    for sim_node in sim_nodes:
        typer.echo(f"Waiting for simulation pod {sim_node}")
        pods[sim_node] = _wait_for_sim_pod(core_ns, sim_node, debug=debug)
    return pods


def _configure_linux_interfaces(
    *,
    core_ns: str,
    configs: Iterable[NodeInterfaceConfig],
    pod_lookup: dict[str, str],
    debug: bool,
) -> None:
    for config in configs:
        vlan_suffix = f".{config.vlan}" if config.vlan else ""
        typer.echo(
            f"Configuring {config.name}: {config.interface}{vlan_suffix} -> {config.ip_address}"
        )
        pod_name = pod_lookup.get(config.name)
        if not pod_name:
            pod_name = _wait_for_sim_pod(core_ns, config.name, debug=debug)
            pod_lookup[config.name] = pod_name
        if config.vlan:
            vlan_iface = f"{config.interface}.{config.vlan}"
            command_str = (
                f"ip link set {config.interface} up"
                f" && (ip link show {vlan_iface} >/dev/null 2>&1 || ip link add link {config.interface} name {vlan_iface} type vlan id {config.vlan})"
                f" && ip link set {vlan_iface} up"
                f" && ip addr flush dev {vlan_iface}"
                f" && ip addr add {config.ip_address} dev {vlan_iface}"
            )
        else:
            command_str = (
                f"ip addr flush dev {config.interface}"
                f" && ip addr add {config.ip_address} dev {config.interface}"
                f" && ip link set {config.interface} up"
            )
        deadline = time.time() + 180
        while True:
            try:
                run_command(
                    (
                        "kubectl",
                        "-n",
                        core_ns,
                        "exec",
                        pod_name,
                        "-c",
                        config.name,
                        "--",
                        "sh",
                        "-c",
                        command_str,
                    )
                )
                break
            except CommandError as exc:
                error_text = str(exc)
                if (
                    any(
                        phrase in error_text
                        for phrase in ("Device \"", "Cannot find device", "does not exist")
                    )
                    and time.time() < deadline
                ):
                    time.sleep(2)
                    continue
                raise

        if debug:
            state = run_command(
                (
                    "kubectl",
                    "-n",
                    core_ns,
                    "exec",
                    pod_name,
                    "-c",
                    config.name,
                    "--",
                    "ip",
                    "addr",
                    "show",
                    config.interface,
                )
            )
            typer.echo(f"Debug: interface state for {config.name}\n{state}")


def _dump_simulation_state(namespace: str) -> None:
    for resource, output_flag in (("simnodes", "-o wide"), ("simlinks", "-o yaml")):
        cmd = (
            "kubectl",
            "-n",
            namespace,
            "get",
            resource,
            *output_flag.split(),
        )
        try:
            output = run_command(cmd)
        except CommandError as exc:
            typer.echo(f"Debug: failed to run {' '.join(cmd)}: {exc}")
            continue
        typer.echo(f"Debug: {' '.join(cmd)}\n{output or '<no output>'}")


def _verify_cleanup(namespace: str, summaries: Iterable[ResourceSummary]) -> None:
    kind_to_resource = {
        "SimNode": "simnode",
        "SimLink": "simlink",
        "TopoLink": "topolink",
    }

    lingering: list[str] = []
    for summary in summaries:
        resource = kind_to_resource.get(summary.kind)
        if resource is None:
            continue

        try:
            run_command(("kubectl", "-n", namespace, "get", resource, summary.name))
        except CommandError:
            continue
        lingering.append(f"{resource}.core.eda.nokia.com/{summary.name}")

    if lingering:
        typer.echo(
            "Warning: lingering simulation resources detected:\n" + "\n".join(lingering)
        )


def apply_simulation(
    *,
    sim_spec_file: Path | None,
    raw_spec: Mapping[str, Any] | None,
    topo_ns: str,
    core_ns: str,
    emit_crds: Path | None,
    debug: bool,
) -> None:
    if sim_spec_file is not None:
        typer.echo(f"Loading simulation spec from {sim_spec_file}")
        raw_spec = read_yaml(sim_spec_file)
    elif raw_spec is not None:
        typer.echo("Using auto-generated simulation spec")
    else:  # pragma: no cover - defensive guard
        raise SpecError("Simulation spec is required")

    simulation_spec = parse_simulation_spec(raw_spec)
    bundle = _render_bundle(simulation_spec, namespace=topo_ns)

    if debug:
        typer.echo("Debug: generated simulation manifest")
        typer.echo(bundle.manifest_text.rstrip())

    manifest_path = _write_manifest(bundle.manifest_text, target=emit_crds)
    typer.echo(f"Applying simulation bundle with ETC using {manifest_path}")
    try:
        typer.echo("Running etc apply --dry-run")
        _run_etc(("etc", "apply", "-f", str(manifest_path), "--dry-run"), debug=debug)
        typer.echo("Running etc apply")
        _run_etc(("etc", "apply", "-f", str(manifest_path)), debug=debug)
    finally:
        if emit_crds is None:
            manifest_path.unlink(missing_ok=True)

    grouped = _group_summaries(bundle.summaries)
    typer.echo("Updated resources:")
    for kind, names in grouped.items():
        typer.echo(f"  {kind}: {', '.join(names)}")

    pod_lookup = _collect_sim_pods(core_ns, bundle.sim_nodes, debug=debug)

    if bundle.node_configs:
        _configure_linux_interfaces(
            core_ns=core_ns,
            configs=bundle.node_configs,
            pod_lookup=pod_lookup,
            debug=debug,
        )
    else:
        typer.echo("No Linux interface configuration required")

    if debug:
        _dump_simulation_state(topo_ns)


def remove_simulation(
    *,
    sim_spec_file: Path | None,
    raw_spec: Mapping[str, Any] | None,
    topo_ns: str,
    core_ns: str,
    debug: bool,
) -> None:
    del core_ns  # core namespace is unused during deletion but kept for CLI symmetry.
    if sim_spec_file is not None:
        typer.echo(f"Loading simulation spec from {sim_spec_file}")
        raw_spec = read_yaml(sim_spec_file)
    elif raw_spec is not None:
        typer.echo("Using auto-generated simulation spec for deletion")
    else:  # pragma: no cover - defensive guard
        raise SpecError("Simulation spec is required")
    simulation_spec = parse_simulation_spec(raw_spec)
    bundle = _render_bundle(simulation_spec, namespace=topo_ns)

    if debug:
        typer.echo("Debug: generated simulation manifest for deletion")
        typer.echo(bundle.manifest_text.rstrip())

    manifest_path = _write_manifest(bundle.manifest_text, target=None)
    typer.echo(f"Deleting simulation bundle with ETC using {manifest_path}")
    try:
        typer.echo("Running etc delete --dry-run")
        _run_etc(("etc", "delete", "-f", str(manifest_path), "--dry-run"), debug=debug)
        typer.echo("Running etc delete")
        _run_etc(("etc", "delete", "-f", str(manifest_path)), debug=debug)
    finally:
        manifest_path.unlink(missing_ok=True)

    if debug:
        _dump_simulation_state(topo_ns)

    _verify_cleanup(topo_ns, bundle.summaries)


__all__ = [
    "apply_simulation",
    "remove_simulation",
]

from __future__ import annotations

"""Typer-based CLI for attaching simulation nodes to fabric edge interfaces."""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping

import typer
import yaml

DEFAULT_TOPO_NS = os.environ.get("TOPO_NS", "eda")
DEFAULT_CORE_NS = os.environ.get("CORE_NS", "eda-system")

app = typer.Typer(help="Manage edge simulation attachments for EDA topologies.")


class CommandError(RuntimeError):
    """Raised when a kubectl invocation fails."""


def run_command(cmd: Iterable[str], *, input_text: str | None = None) -> str:
    """Run a command and return stdout, raising if it fails."""

    process = subprocess.run(
        list(cmd),
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        raise CommandError(
            "\n".join(
                (
                    f"Command failed: {' '.join(cmd)}",
                    f"stdout: {process.stdout.strip()}",
                    f"stderr: {process.stderr.strip()}",
                )
            )
        )
    return process.stdout.strip()


def find_toolbox_pod(core_ns: str) -> str:
    jsonpath = "{.items[0].metadata.name}"
    cmd = (
        "kubectl",
        "-n",
        core_ns,
        "get",
        "pods",
        "-l",
        "eda.nokia.com/app=eda-toolbox",
        "-o",
        f"jsonpath={jsonpath}",
    )
    pod_name = run_command(cmd)
    if not pod_name:
        raise CommandError(f"No eda-toolbox pod found in namespace {core_ns}")
    return pod_name


def ensure_list(value: Any, *, key: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return list(value)
    raise ValueError(f"Expected list for '{key}', got {type(value).__name__}")


def normalize_sim_node(node: Mapping[str, Any]) -> dict[str, Any]:
    name = node.get("name")
    image = node.get("image")
    node_type = node.get("type", "Linux")

    if not name or not isinstance(name, str):
        raise ValueError("Each simNode requires a string 'name'")
    if not image or not isinstance(image, str):
        raise ValueError(f"simNode '{name}' requires a string 'image'")

    normalized: dict[str, Any] = {
        "name": name,
        "image": image,
        "type": node_type,
    }

    for key, value in node.items():
        if key not in normalized:
            normalized[key] = value

    return normalized


def normalize_topology_entry(
    entry: Mapping[str, Any], *, known_nodes: set[str] | None
) -> dict[str, Any]:
    required = ("node", "interface", "simNode", "simNodeInterface")
    missing = [key for key in required if not entry.get(key)]
    if missing:
        raise ValueError(
            "Topology entries require keys: node, interface, simNode, simNodeInterface"
        )

    node_name = entry.get("node")
    if (
        known_nodes is not None
        and isinstance(node_name, str)
        and node_name not in known_nodes
    ):
        sorted_nodes = ", ".join(sorted(known_nodes)) or "<none>"
        raise ValueError(
            f"Topology entry references unknown node '{node_name}'."
            f" Available fabric nodes: {sorted_nodes}"
        )

    return dict(entry)


def normalize_sim_spec(
    raw: Mapping[str, Any], *, known_nodes: set[str] | None = None
) -> dict[str, Any]:
    """Return data in the shape the API expects."""

    if "items" in raw:
        return dict(raw)

    spec_section: MutableMapping[str, Any]
    if "spec" in raw:
        spec_section = dict(raw.get("spec") or {})
    else:
        spec_section = dict(raw)

    sim_nodes = ensure_list(spec_section.get("simNodes"), key="simNodes")
    topology = ensure_list(spec_section.get("topology"), key="topology")

    if not sim_nodes:
        raise ValueError("At least one sim node must be provided")
    if not topology:
        raise ValueError("At least one topology attachment must be provided")

    normalized_nodes = [normalize_sim_node(node) for node in sim_nodes]
    normalized_links = [
        normalize_topology_entry(entry, known_nodes=known_nodes)
        for entry in topology
    ]

    return {
        "items": [
            {
                "spec": {
                    "simNodes": normalized_nodes,
                    "topology": normalized_links,
                }
            }
        ]
    }


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"YAML file not found: {path}") from exc

    if data is None:
        raise ValueError(f"YAML file {path} is empty")
    if not isinstance(data, Mapping):
        raise ValueError(f"YAML file {path} must contain a mapping at its root")
    return dict(data)


def write_json_temp(data: Mapping[str, Any], filename: str, directory: Path) -> Path:
    target = directory / filename
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    return target


def copy_to_toolbox(core_ns: str, toolbox_pod: str, local_path: Path, remote_name: str) -> None:
    cmd = (
        "kubectl",
        "-n",
        core_ns,
        "cp",
        str(local_path),
        f"{toolbox_pod}:/tmp/{remote_name}",
    )
    run_command(cmd)


def exec_in_toolbox(core_ns: str, toolbox_pod: str, command: Iterable[str]) -> None:
    cmd = (
        "kubectl",
        "-n",
        core_ns,
        "exec",
        toolbox_pod,
        "--",
        *command,
    )
    run_command(cmd)


def load_namespace_resource(namespace: str, resource: str) -> list[dict[str, Any]]:
    raw = run_command(
        (
            "kubectl",
            "-n",
            namespace,
            "get",
            resource,
            "-o",
            "json",
        )
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise ValueError(f"Failed to decode JSON from kubectl {resource}: {raw}") from exc

    items = payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError(f"Unexpected payload for {resource}: missing 'items' list")
    return items


def fetch_existing_topology(namespace: str) -> dict[str, Any]:
    nodes_raw = load_namespace_resource(namespace, "toponodes")
    if not nodes_raw:
        raise ValueError(
            "No TopoNode resources found; provide --topology to avoid clearing the fabric."
        )

    links_raw = load_namespace_resource(namespace, "topolinks")

    nodes: list[dict[str, Any]] = []
    for node in nodes_raw:
        metadata = node.get("metadata", {})
        spec = node.get("spec", {})
        name = metadata.get("name")
        if not name:
            continue

        node_entry: dict[str, Any] = {"name": name}
        labels = metadata.get("labels") or {}
        if labels:
            node_entry["labels"] = labels

        node_spec: dict[str, Any] = {}
        for key in ("operatingSystem", "platform", "version", "nodeProfile"):
            if key in spec:
                node_spec[key] = spec[key]
        if "npp" in spec:
            node_spec["npp"] = spec["npp"]
        if "productionAddress" in spec:
            node_spec["productionAddress"] = spec["productionAddress"]
        if node_spec:
            node_entry["spec"] = node_spec

        nodes.append(node_entry)

    links: list[dict[str, Any]] = []
    for link in links_raw:
        metadata = link.get("metadata", {})
        spec = link.get("spec", {})
        name = metadata.get("name")
        if not name:
            continue

        link_entry: dict[str, Any] = {"name": name}
        labels = metadata.get("labels") or {}
        if labels:
            link_entry["labels"] = labels

        if "encapType" in spec:
            link_entry["encapType"] = spec["encapType"]

        if "links" in spec:
            link_entry.setdefault("spec", {})["links"] = spec["links"]

        links.append(link_entry)

    return {"items": [{"spec": {"nodes": nodes, "links": links}}]}


def extract_node_names(topology_data: Mapping[str, Any]) -> set[str]:
    items = topology_data.get("items") if isinstance(topology_data, Mapping) else None
    if not isinstance(items, list):
        return set()

    names: set[str] = set()
    for item in items:
        spec = item.get("spec") if isinstance(item, Mapping) else None
        nodes = spec.get("nodes") if isinstance(spec, Mapping) else None
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            name = node.get("name")
            if isinstance(name, str):
                names.add(name)
    return names


def apply_topology(
    *,
    topology_file: Path | None,
    sim_spec_file: Path,
    topo_ns: str,
    core_ns: str,
) -> None:
    topology_data: dict[str, Any] | None = None
    if topology_file is not None:
        topology_data = read_yaml(topology_file)
    else:
        typer.echo(
            f"Collecting existing fabric definition from TopoNode/TopoLink resources in namespace {topo_ns}"
        )
        topology_data = fetch_existing_topology(topo_ns)

    known_nodes = extract_node_names(topology_data) if topology_data else None

    sim_spec = read_yaml(sim_spec_file)
    normalized_sim = normalize_sim_spec(sim_spec, known_nodes=known_nodes)

    toolbox_pod = find_toolbox_pod(core_ns)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        sim_json = write_json_temp(normalized_sim, "simtopo.json", tmpdir)
        topo_json: Path | None = None
        if topology_data is not None:
            topo_json = write_json_temp(topology_data, "topo.json", tmpdir)
            typer.echo(
                f"Copying topology JSON to {core_ns}/{toolbox_pod}:/tmp/topo.json"
            )
            copy_to_toolbox(core_ns, toolbox_pod, topo_json, "topo.json")
        else:
            typer.echo("Skipping fabric topology update; existing config will be reused.")

        typer.echo(
            f"Copying simulation JSON to {core_ns}/{toolbox_pod}:/tmp/simtopo.json"
        )
        copy_to_toolbox(core_ns, toolbox_pod, sim_json, "simtopo.json")

    typer.echo("Applying topology via api-server-topo")
    command = ["api-server-topo", "-n", topo_ns]
    if topology_data is not None:
        command.extend(["-f", "/tmp/topo.json"])
    command.extend(["-s", "/tmp/simtopo.json"])
    exec_in_toolbox(core_ns, toolbox_pod, command)


def remove_sim_spec(*, topo_ns: str, core_ns: str) -> None:
    wipe_sim = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: eda-topology-sim
data:
  sim.yaml: |
    {}
"""
    typer.echo(f"Resetting eda-topology-sim ConfigMap in namespace {topo_ns}")
    run_command(("kubectl", "-n", topo_ns, "apply", "-f", "-"), input_text=wipe_sim)

    topology_data = fetch_existing_topology(topo_ns)

    toolbox_pod = find_toolbox_pod(core_ns)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        topo_json = write_json_temp(topology_data, "topo.json", tmpdir)
        typer.echo(
            f"Copying topology JSON to {core_ns}/{toolbox_pod}:/tmp/topo.json for refresh"
        )
        copy_to_toolbox(core_ns, toolbox_pod, topo_json, "topo.json")

    typer.echo("Triggering api-server-topo refresh")
    exec_in_toolbox(
        core_ns,
        toolbox_pod,
        ("api-server-topo", "-n", topo_ns, "-f", "/tmp/topo.json"),
    )


def _handle_error(exc: Exception) -> None:
    typer.secho(f"Error: {exc}", err=True, fg=typer.colors.RED)
    raise typer.Exit(1)


@app.command()
def apply(
    
    topology: Path | None = typer.Option(
        None,
        "--topology",
        "-t",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Optional path to the fabric topology YAML file; omit to leave existing fabric untouched.",
    ),
    spec: Path = typer.Option(
        ...,
        "--spec",
        "-s",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the simplified simulation spec YAML file",
    ),
    topology_namespace: str = typer.Option(
        DEFAULT_TOPO_NS,
        "--topology-namespace",
        "-n",
        envvar="TOPO_NS",
        help="Namespace containing the EDA topology ConfigMaps.",
    ),
    core_namespace: str = typer.Option(
        DEFAULT_CORE_NS,
        "--core-namespace",
        "-c",
        envvar="CORE_NS",
        help="Namespace hosting the eda-toolbox pod.",
    ),
) -> None:
    """Load topology plus simulation attachments defined in YAML."""

    try:
        apply_topology(
            topology_file=topology,
            sim_spec_file=spec,
            topo_ns=topology_namespace,
            core_ns=core_namespace,
        )
    except (CommandError, ValueError, FileNotFoundError) as exc:  # pragma: no cover
        _handle_error(exc)


@app.command()
def remove(
    topology_namespace: str = typer.Option(
        DEFAULT_TOPO_NS,
        "--topology-namespace",
        "-n",
        envvar="TOPO_NS",
        help="Namespace containing the EDA topology ConfigMaps.",
    ),
    core_namespace: str = typer.Option(
        DEFAULT_CORE_NS,
        "--core-namespace",
        "-c",
        envvar="CORE_NS",
        help="Namespace hosting the eda-toolbox pod.",
    ),
) -> None:
    """Remove simulation attachments without altering the fabric topology."""

    try:
        remove_sim_spec(topo_ns=topology_namespace, core_ns=core_namespace)
    except (CommandError, ValueError, FileNotFoundError) as exc:  # pragma: no cover
        _handle_error(exc)


if __name__ == "__main__":  # pragma: no cover
    app()

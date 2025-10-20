"""Topology helpers and operations for cx-attach."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import typer

from .kubectl import (
    copy_to_toolbox,
    exec_in_toolbox,
    find_toolbox_pod,
    load_namespace_resource,
    run_command,
)
from .specs import normalize_sim_spec, read_yaml


def _build_node_entry(node: Mapping[str, Any]) -> dict[str, Any] | None:
    metadata = node.get("metadata") if isinstance(node, Mapping) else None
    spec = node.get("spec") if isinstance(node, Mapping) else None
    if not isinstance(metadata, Mapping):
        return None

    name = metadata.get("name")
    if not isinstance(name, str):
        return None

    entry: dict[str, Any] = {"name": name}

    labels = metadata.get("labels")
    if isinstance(labels, Mapping) and labels:
        entry["labels"] = dict(labels)

    spec_mapping = spec if isinstance(spec, Mapping) else {}
    node_spec: dict[str, Any] = {
        key: spec_mapping[key]
        for key in ("operatingSystem", "platform", "version", "nodeProfile")
        if key in spec_mapping
    }
    for optional in ("npp", "productionAddress"):
        if optional in spec_mapping:
            node_spec[optional] = spec_mapping[optional]
    if node_spec:
        entry["spec"] = node_spec

    return entry


def _build_link_entry(link: Mapping[str, Any]) -> dict[str, Any] | None:
    metadata = link.get("metadata") if isinstance(link, Mapping) else None
    spec = link.get("spec") if isinstance(link, Mapping) else None
    if not isinstance(metadata, Mapping):
        return None

    name = metadata.get("name")
    if not isinstance(name, str):
        return None

    entry: dict[str, Any] = {"name": name}

    labels = metadata.get("labels")
    if isinstance(labels, Mapping) and labels:
        entry["labels"] = dict(labels)

    spec_mapping = spec if isinstance(spec, Mapping) else {}
    if "encapType" in spec_mapping:
        entry["encapType"] = spec_mapping["encapType"]

    links_value = spec_mapping.get("links")
    if isinstance(links_value, list):
        entry.setdefault("spec", {})["links"] = links_value

    return entry


def fetch_existing_topology(namespace: str) -> dict[str, Any]:
    nodes_raw = load_namespace_resource(namespace, "toponodes")
    if not nodes_raw:
        raise ValueError(
            "No TopoNode resources found; provide --topology to avoid clearing the fabric."
        )

    links_raw = load_namespace_resource(namespace, "topolinks")

    nodes = [
        node_entry
        for raw in nodes_raw
        if (node_entry := _build_node_entry(raw)) is not None
    ]

    links = [
        link_entry
        for raw in links_raw
        if (link_entry := _build_link_entry(raw)) is not None
    ]

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


def write_json_temp(data: Mapping[str, Any], filename: str, directory: Path) -> Path:
    target = directory / filename
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    return target


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

    typer.echo(f"Loading simulation spec from {sim_spec_file}")
    raw_spec = read_yaml(sim_spec_file)
    normalized_sim = normalize_sim_spec(raw_spec, known_nodes=known_nodes)

    toolbox_pod = find_toolbox_pod(core_ns)
    typer.echo(f"Using toolbox pod {core_ns}/{toolbox_pod}")

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


__all__ = [
    "apply_topology",
    "extract_node_names",
    "fetch_existing_topology",
    "remove_sim_spec",
]

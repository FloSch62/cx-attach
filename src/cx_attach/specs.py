"""Helpers for parsing and validating simulation specifications."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path
from typing import Any

import yaml


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
    spec_section = (
        dict(raw.get("spec") or {}) if "spec" in raw else dict(raw)
    )

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


__all__ = [
    "ensure_list",
    "normalize_sim_node",
    "normalize_sim_spec",
    "normalize_topology_entry",
    "read_yaml",
]

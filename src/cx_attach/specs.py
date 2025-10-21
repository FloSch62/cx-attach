"""Helpers for parsing and validating simulation specifications."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SIMNODE_ALLOWED_FIELDS = {
    "component",
    "containerImage",
    "dhcp",
    "imagePullSecret",
    "license",
    "operatingSystem",
    "platform",
    "platformPath",
    "port",
    "serialNumberPath",
    "version",
    "versionMatch",
    "versionPath",
}


@dataclass(frozen=True)
class SimNodeSpec:
    """Simulation node definition from the simplified spec."""

    name: str
    image: str
    node_type: str
    raw: Mapping[str, Any]

    @property
    def ip_address(self) -> str | None:
        value = self.raw.get("ipAddress") or self.raw.get("ip")
        return str(value) if isinstance(value, (str, int)) else None

    @property
    def vlan(self) -> str | None:
        value = self.raw.get("vlan") or self.raw.get("vlanId")
        return str(value) if isinstance(value, (str, int)) else None

    @property
    def interface(self) -> str | None:
        value = self.raw.get("interface") or self.raw.get("simInterface")
        return str(value) if isinstance(value, str) and value.strip() else None

    @property
    def spec_overrides(self) -> Mapping[str, Any]:
        overrides = self.raw.get("spec")
        if not isinstance(overrides, Mapping):
            return {}
        return {
            key: value
            for key, value in overrides.items()
            if key in SIMNODE_ALLOWED_FIELDS and value is not None
        }


@dataclass(frozen=True)
class AttachmentSpec:
    """Fabric attachment definition connecting fabric and sim node."""

    fabric_node: str
    fabric_interface: str
    sim_node: str
    sim_interface: str
    vlan: str | None


@dataclass(frozen=True)
class SimulationSpec:
    """Parsed simulation specification with lookup helpers."""

    nodes: dict[str, SimNodeSpec]
    attachments: list[AttachmentSpec]

    @property
    def ordered_nodes(self) -> list[SimNodeSpec]:
        return list(self.nodes.values())


class SpecError(ValueError):
    """Raised when the simulation spec is invalid."""


def _ensure_list(value: Any, *, key: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return list(value)
    raise SpecError(f"Expected list for '{key}', got {type(value).__name__}")


def _parse_sim_nodes(raw_nodes: Iterable[Any]) -> dict[str, SimNodeSpec]:
    nodes: dict[str, SimNodeSpec] = {}
    for entry in raw_nodes:
        if not isinstance(entry, Mapping):
            raise SpecError("Entries under 'simNodes' must be mappings")
        name = entry.get("name")
        image = entry.get("image")
        node_type = entry.get("type", "linux")
        if not isinstance(name, str) or not name.strip():
            raise SpecError("Each simNode requires a string 'name'")
        if name in nodes:
            raise SpecError(f"Duplicate simNode name '{name}' detected")
        if not isinstance(image, str) or not image.strip():
            raise SpecError(f"simNode '{name}' requires a string 'image'")
        if not isinstance(node_type, str) or not node_type.strip():
            raise SpecError(f"simNode '{name}' requires a valid 'type'")
        nodes[name] = SimNodeSpec(
            name=name.strip(),
            image=image.strip(),
            node_type=node_type.strip(),
            raw=dict(entry),
        )
    if not nodes:
        raise SpecError("At least one simNode must be provided")
    return nodes


def _parse_attachments(
    raw_attachments: Iterable[Any],
    *,
    known_nodes: dict[str, SimNodeSpec],
) -> list[AttachmentSpec]:
    attachments: list[AttachmentSpec] = []
    if not known_nodes:
        return attachments

    for entry in raw_attachments:
        if not isinstance(entry, Mapping):
            raise SpecError("Entries under 'topology' must be mappings")
        fabric_node = entry.get("node")
        fabric_iface = entry.get("interface")
        sim_node = entry.get("simNode")
        sim_iface = entry.get("simNodeInterface") or entry.get("interface")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (fabric_node, fabric_iface, sim_node, sim_iface)
        ):
            raise SpecError(
                "Topology entries require node/interface pairs on both fabric and sim sides"
            )
        if sim_node not in known_nodes:
            known = ", ".join(sorted(known_nodes)) or "<none>"
            raise SpecError(
                f"Topology entry references unknown simNode '{sim_node}'."
                f" Available simNodes: {known}"
            )
        vlan_value = entry.get("vlan") or entry.get("vlanId")
        vlan = str(vlan_value) if isinstance(vlan_value, (str, int)) else None
        attachments.append(
            AttachmentSpec(
                fabric_node=fabric_node.strip(),
                fabric_interface=fabric_iface.strip(),
                sim_node=sim_node.strip(),
                sim_interface=sim_iface.strip(),
                vlan=vlan,
            )
        )
    if not attachments:
        raise SpecError("At least one topology attachment must be provided")
    return attachments


def parse_simulation_spec(raw: Mapping[str, Any]) -> SimulationSpec:
    """Convert raw YAML content into a validated SimulationSpec."""

    if "items" in raw:
        items = raw.get("items")
        if not isinstance(items, list) or not items:
            raise SpecError("'items' must contain at least one entry")
        base = items[0].get("spec") if isinstance(items[0], Mapping) else None
        if not isinstance(base, Mapping):
            raise SpecError("items[0].spec must be a mapping")
    else:
        base = raw.get("spec") if isinstance(raw.get("spec"), Mapping) else raw

    sim_nodes_raw = _ensure_list(base.get("simNodes"), key="simNodes")
    topology_raw = _ensure_list(base.get("topology"), key="topology")

    nodes = _parse_sim_nodes(sim_nodes_raw)
    attachments = _parse_attachments(topology_raw, known_nodes=nodes)
    return SimulationSpec(nodes=nodes, attachments=attachments)


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"YAML file not found: {path}") from exc

    if data is None:
        raise SpecError(f"YAML file {path} is empty")
    if not isinstance(data, Mapping):
        raise SpecError(f"YAML file {path} must contain a mapping at its root")
    return dict(data)


__all__ = [
    "AttachmentSpec",
    "SimNodeSpec",
    "SimulationSpec",
    "SpecError",
    "parse_simulation_spec",
    "read_yaml",
]

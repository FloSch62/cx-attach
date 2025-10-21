"""Auto-generation of simulation specs from cluster resources."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from ipaddress import IPv4Interface
from typing import Any

from .kubectl import CommandError, run_command
from .specs import SpecError

DEFAULT_IMAGE = "ghcr.io/srl-labs/network-multitool:v0.4.1"
DEFAULT_NODE_TYPE = "Linux"
DEFAULT_SIM_INTERFACE = "eth1"
MAX_NAME_LENGTH = 63


@dataclass(frozen=True)
class AutoAttachment:
    """Attachment inferred from VirtualNetwork and Interface resources."""

    virtual_network: str
    vlan_name: str
    vlan_id: str | None
    interface_name: str
    fabric_node: str
    fabric_interface: str
    sim_name: str
    ip_address: str | None


@dataclass(frozen=True)
class AutoPlan:
    """Plan containing the synthesized spec and attachment details."""

    raw_spec: dict[str, Any]
    attachments: list[AutoAttachment]

    @property
    def sim_node_names(self) -> list[str]:
        nodes = self.raw_spec.get("simNodes", [])
        return [node.get("name", "") for node in nodes if isinstance(node, dict)]


@dataclass(frozen=True)
class VlanDefinition:
    """Normalised VLAN slice extracted from a VirtualNetwork."""

    virtual_network: str
    vlan_name: str
    vlan_id: str | None
    selectors: list[str]
    ip_pool: Iterator[str] | None


def _load_resource_items(namespace: str, resource: str) -> list[dict[str, Any]]:
    try:
        payload = run_command(
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
    except CommandError as exc:  # pragma: no cover - depends on cluster state
        raise SpecError(
            f"Failed to load {resource} from namespace {namespace}: {exc}"
        ) from exc
    data = json.loads(payload or "{}")
    items = data.get("items", [])
    return [item for item in items if isinstance(item, dict)]


def _collect_interfaces(namespace: str) -> dict[str, dict[str, Any]]:
    interfaces = _load_resource_items(namespace, "interface")
    return {item.get("metadata", {}).get("name", ""): item for item in interfaces}


def _parse_selector(selector: str) -> tuple[str, str | None]:
    if "=" in selector:
        key, value = selector.split("=", 1)
        return key.strip(), value.strip()
    return selector.strip(), None


def _matches_selector(interface: dict[str, Any], selector: str) -> bool:
    key, expected = _parse_selector(selector)
    if not key:
        return False
    labels = interface.get("metadata", {}).get("labels", {})
    if not isinstance(labels, dict):
        return False
    if key not in labels:
        return False
    if expected is None:
        return True
    return str(labels.get(key)) == expected


def _extract_fabric_endpoint(interface: dict[str, Any]) -> tuple[str | None, str | None]:
    spec = interface.get("spec") if isinstance(interface, dict) else None
    members = spec.get("members") if isinstance(spec, dict) else None
    if not isinstance(members, list) or not members:
        return (None, None)
    primary = members[0]
    node = primary.get("node") if isinstance(primary, dict) else None
    iface = primary.get("interface") if isinstance(primary, dict) else None
    if isinstance(node, str) and isinstance(iface, str):
        return (node, iface)
    return (None, None)


def _selectors_from_vlan_spec(vlan_spec: dict[str, Any]) -> list[str]:
    selector_value = vlan_spec.get("interfaceSelector")
    if isinstance(selector_value, str):
        return [selector_value]
    if isinstance(selector_value, list):
        return [selector for selector in selector_value if isinstance(selector, str)]
    return []


def _build_ip_allocators(virtual_network: dict[str, Any]) -> dict[str, Iterator[str]]:
    allocators: dict[str, Any] = {}
    spec = virtual_network.get("spec") if isinstance(virtual_network, dict) else None
    irb_interfaces = spec.get("irbInterfaces") if isinstance(spec, dict) else None
    if not isinstance(irb_interfaces, list):
        return allocators
    for entry in irb_interfaces:
        if not isinstance(entry, dict):
            continue
        spec_entry = entry.get("spec") if isinstance(entry.get("spec"), dict) else {}
        bridge_domain = spec_entry.get("bridgeDomain")
        ip_addresses = spec_entry.get("ipAddresses")
        if not isinstance(bridge_domain, str) or not isinstance(ip_addresses, list):
            continue
        primary = None
        for ip_entry in ip_addresses:
            if not isinstance(ip_entry, dict):
                continue
            ipv4 = ip_entry.get("ipv4Address")
            if not isinstance(ipv4, dict):
                continue
            ip_prefix = ipv4.get("ipPrefix")
            if not isinstance(ip_prefix, str):
                continue
            iface = IPv4Interface(ip_prefix)
            primary = iface
            if ip_entry.get("primary", False):
                primary = iface
                break
        if primary is None:
            continue
        allocators[bridge_domain] = _ip_allocator(primary)
    return allocators


def _ip_allocator(ip_iface: IPv4Interface):
    network = ip_iface.network
    gateway = ip_iface.ip
    prefixlen = network.prefixlen
    def _generator():
        for host in network.hosts():
            if host == gateway:
                continue
            yield f"{host}/{prefixlen}"
    return _generator()


def _matching_interfaces(
    selectors: list[str], interfaces: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for name, interface in interfaces.items():
        if not name:
            continue
        for selector in selectors:
            if _matches_selector(interface, selector):
                matches[name] = interface
                break
    return matches


def _gather_vlan_definitions(vn: dict[str, Any]) -> list[VlanDefinition]:
    spec = vn.get("spec") if isinstance(vn, dict) else None
    if not isinstance(spec, dict):
        return []
    vlans = spec.get("vlans")
    if not isinstance(vlans, list):
        return []

    vn_name = vn.get("metadata", {}).get("name", "virtualnetwork")
    ip_allocators = _build_ip_allocators(vn)
    definitions: list[VlanDefinition] = []
    for vlan_entry in vlans:
        if not isinstance(vlan_entry, dict):
            continue
        vlan_name = vlan_entry.get("name") or vn_name
        vlan_spec = vlan_entry.get("spec") if isinstance(vlan_entry.get("spec"), dict) else {}
        selectors = _selectors_from_vlan_spec(vlan_spec)
        if not selectors:
            continue
        bridge_domain = vlan_spec.get("bridgeDomain")
        vlan_id = vlan_spec.get("vlanID") or vlan_spec.get("vlanId")
        ip_pool = (
            ip_allocators.get(bridge_domain)
            if isinstance(bridge_domain, str)
            else None
        )
        definitions.append(
            VlanDefinition(
                virtual_network=vn_name,
                vlan_name=str(vlan_name),
                vlan_id=str(vlan_id) if vlan_id is not None else None,
                selectors=selectors,
                ip_pool=ip_pool,
            )
        )
    return definitions


def _generate_sim_name(interface_name: str) -> str:
    base = interface_name.lower().replace("_", "-")
    if len(base) <= MAX_NAME_LENGTH:
        return base
    return base[:MAX_NAME_LENGTH]


def _merge_attachments(
    sim_nodes: dict[str, dict[str, Any]],
    attachments: list[AutoAttachment],
) -> list[AutoAttachment]:
    grouped: dict[str, list[AutoAttachment]] = {}
    for attachment in attachments:
        grouped.setdefault(attachment.interface_name, []).append(attachment)

    merged: list[AutoAttachment] = []
    for interface_name, entries in grouped.items():
        entries_sorted = sorted(
            entries,
            key=lambda att: (
                0 if att.ip_address else 1,
                att.vlan_id or "",
                att.virtual_network,
            ),
        )
        primary = entries_sorted[0]
        node_entry = sim_nodes.get(primary.sim_name)
        if node_entry is not None:
            if primary.vlan_id and "vlan" not in node_entry:
                node_entry["vlan"] = primary.vlan_id
            if primary.ip_address and "ipAddress" not in node_entry:
                node_entry["ipAddress"] = primary.ip_address
        merged.append(primary)
    return merged


def _collect_auto_attachments(
    virtual_networks: list[dict[str, Any]],
    interfaces: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[AutoAttachment]]:
    attachments: list[AutoAttachment] = []
    sim_nodes: dict[str, dict[str, Any]] = {}
    seen_pairs: set[tuple[str, str]] = set()

    for vn in virtual_networks:
        for definition in _gather_vlan_definitions(vn):
            matches = _matching_interfaces(definition.selectors, interfaces)
            for interface_name, interface in matches.items():
                pair_key = (definition.vlan_name, interface_name)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                fabric_node, fabric_interface = _extract_fabric_endpoint(interface)
                if not fabric_node or not fabric_interface:
                    continue
                sim_name = _generate_sim_name(interface_name)
                ip_address = None
                if definition.ip_pool is not None:
                    try:
                        ip_address = next(definition.ip_pool)
                    except StopIteration:
                        ip_address = None

                node_entry = sim_nodes.setdefault(
                    sim_name,
                    {
                        "name": sim_name,
                        "image": DEFAULT_IMAGE,
                        "type": DEFAULT_NODE_TYPE,
                        "interface": DEFAULT_SIM_INTERFACE,
                    },
                )
                if definition.vlan_id and "vlan" not in node_entry:
                    node_entry["vlan"] = definition.vlan_id
                if ip_address and "ipAddress" not in node_entry:
                    node_entry["ipAddress"] = ip_address

                attachments.append(
                    AutoAttachment(
                        virtual_network=definition.virtual_network,
                        vlan_name=definition.vlan_name,
                        vlan_id=definition.vlan_id,
                        interface_name=interface_name,
                        fabric_node=fabric_node,
                        fabric_interface=fabric_interface,
                        sim_name=sim_name,
                        ip_address=ip_address,
                        )
                )
    attachments = _merge_attachments(sim_nodes, attachments)
    return sim_nodes, attachments


def _rename_servers(
    sim_nodes: dict[str, dict[str, Any]],
    attachments: list[AutoAttachment],
) -> tuple[dict[str, dict[str, Any]], list[AutoAttachment]]:
    ordered_attachments = sorted(
        attachments,
        key=lambda a: (
            a.fabric_node,
            a.fabric_interface,
            a.virtual_network,
            a.vlan_name,
            a.interface_name,
        ),
    )

    name_map: dict[str, str] = {}
    renamed_nodes: dict[str, dict[str, Any]] = {}
    renamed_attachments: list[AutoAttachment] = []

    for attachment in ordered_attachments:
        old_name = attachment.sim_name
        new_name = name_map.get(old_name)
        if new_name is None:
            index = len(name_map) + 1
            new_name = f"server{index}"
            name_map[old_name] = new_name

            node_entry = dict(sim_nodes.get(old_name, {}))
            if not node_entry:
                node_entry = {
                    "name": new_name,
                    "image": DEFAULT_IMAGE,
                    "type": DEFAULT_NODE_TYPE,
                    "interface": DEFAULT_SIM_INTERFACE,
                }
            node_entry["name"] = new_name
            renamed_nodes[new_name] = node_entry

        renamed_attachments.append(
            AutoAttachment(
                virtual_network=attachment.virtual_network,
                vlan_name=attachment.vlan_name,
                vlan_id=attachment.vlan_id,
                interface_name=attachment.interface_name,
                fabric_node=attachment.fabric_node,
                fabric_interface=attachment.fabric_interface,
                sim_name=new_name,
                ip_address=attachment.ip_address,
            )
        )

    return renamed_nodes, renamed_attachments


def build_auto_plan(*, topo_ns: str) -> AutoPlan:
    virtual_networks = _load_resource_items(topo_ns, "virtualnetwork")
    if not virtual_networks:
        raise SpecError(f"No VirtualNetwork resources found in namespace {topo_ns}")

    interfaces = _collect_interfaces(topo_ns)
    if not interfaces:
        raise SpecError(f"No Interface resources found in namespace {topo_ns}")

    sim_nodes, attachments = _collect_auto_attachments(virtual_networks, interfaces)

    if not attachments:
        raise SpecError(
            "No interfaces matched the VirtualNetwork selectors; cannot synthesise simulation spec."
        )

    renamed_nodes, renamed_attachments = _rename_servers(sim_nodes, attachments)

    sim_nodes_list = [
        renamed_nodes[f"server{index}"]
        for index in range(1, len(renamed_nodes) + 1)
        if f"server{index}" in renamed_nodes
    ]

    raw_spec = {
        "simNodes": sim_nodes_list,
        "topology": [
            {
                "node": attachment.fabric_node,
                "interface": attachment.fabric_interface,
                "simNode": attachment.sim_name,
                "simNodeInterface": DEFAULT_SIM_INTERFACE,
                **({"vlan": attachment.vlan_id} if attachment.vlan_id else {}),
            }
            for attachment in renamed_attachments
        ],
    }
    return AutoPlan(raw_spec=raw_spec, attachments=renamed_attachments)


__all__ = ["AutoAttachment", "AutoPlan", "VlanDefinition", "build_auto_plan"]

import pytest

import cx_attach.topology as topology
from cx_attach import auto
from cx_attach.topology import NodeInterfaceConfig, _configure_linux_interfaces
from cx_attach.specs import parse_simulation_spec


@pytest.fixture
def mock_cluster_state(monkeypatch):
    virtual_networks = [
        {
            "metadata": {"name": "macvrf1001"},
            "spec": {
                "vlans": [
                    {
                        "name": "macvrf1001",
                        "spec": {
                            "bridgeDomain": "macvrf1001",
                            "interfaceSelector": ["eda.nokia.com/macvrf1001"],
                            "vlanID": "1001",
                        },
                    }
                ]
            },
        },
        {
            "metadata": {"name": "ipvrf2001"},
            "spec": {
                "irbInterfaces": [
                    {
                        "spec": {
                            "bridgeDomain": "macvrf201",
                            "ipAddresses": [
                                {
                                    "ipv4Address": {
                                        "ipPrefix": "10.20.1.254/24",
                                        "primary": True,
                                    }
                                }
                            ],
                        }
                    },
                    {
                        "spec": {
                            "bridgeDomain": "macvrf202",
                            "ipAddresses": [
                                {
                                    "ipv4Address": {
                                        "ipPrefix": "10.20.2.254/24",
                                        "primary": True,
                                    }
                                }
                            ],
                        }
                    },
                ],
                "vlans": [
                    {
                        "name": "macvrf201",
                        "spec": {
                            "bridgeDomain": "macvrf201",
                            "interfaceSelector": ["eda.nokia.com/macvrf201"],
                            "vlanID": "201",
                        },
                    },
                    {
                        "name": "macvrf202",
                        "spec": {
                            "bridgeDomain": "macvrf202",
                            "interfaceSelector": ["eda.nokia.com/macvrf202"],
                            "vlanID": "202",
                        },
                    },
                ],
            },
        },
    ]

    def make_interface(name: str, node: str, selectors: list[str]):
        labels = {selector: "true" for selector in selectors}
        labels["eda.nokia.com/role"] = "edge"
        return {
            "metadata": {
                "name": name,
                "labels": labels,
            },
            "spec": {
                "members": [
                    {
                        "node": node,
                        "interface": "ethernet-1-1",
                    }
                ]
            },
        }

    interfaces = [
        make_interface(
            "demo-leaf-1-ethernet-1-1",
            "demo-leaf-1",
            ["eda.nokia.com/macvrf1001", "eda.nokia.com/macvrf201"],
        ),
        make_interface(
            "demo-leaf-2-ethernet-1-1",
            "demo-leaf-2",
            ["eda.nokia.com/macvrf1001", "eda.nokia.com/macvrf202"],
        ),
    ]

    def fake_load(namespace: str, resource: str):
        assert namespace == "eda"
        if resource == "virtualnetwork":
            return virtual_networks
        if resource == "interface":
            return interfaces
        raise AssertionError(f"unexpected resource {resource}")

    monkeypatch.setattr(auto, "_load_resource_items", fake_load)


def test_auto_plan_preserves_vlan_selectors(mock_cluster_state):
    plan = auto.build_auto_plan(topo_ns="eda")

    vlan1001_attachments = [
        attachment for attachment in plan.attachments if attachment.vlan_id == "1001"
    ]
    assert len(vlan1001_attachments) == 2
    assert all(att.ip_address for att in vlan1001_attachments)
    assert all(att.gateway is None for att in vlan1001_attachments)

    gateways = {node["name"]: node.get("gateway") for node in plan.raw_spec["simNodes"]}
    assert gateways == {"server1": "10.20.1.254", "server2": "10.20.2.254"}

    vlan_pairs = {
        (entry["simNode"], entry.get("vlan")) for entry in plan.raw_spec["topology"]
    }
    assert ("server1", "1001") in vlan_pairs
    assert ("server2", "1001") in vlan_pairs

    vlan1001_entry = next(
        entry for entry in plan.raw_spec["topology"] if entry["vlan"] == "1001" and entry["simNode"] == "server1"
    )
    assert "ipAddress" in vlan1001_entry and vlan1001_entry["ipAddress"].startswith("172.")
    assert "gateway" not in vlan1001_entry

    vlan201_entry = next(
        entry for entry in plan.raw_spec["topology"] if entry["vlan"] == "201" and entry["simNode"] == "server1"
    )
    assert vlan201_entry["gateway"] == "10.20.1.254"


def test_rendered_bundle_uses_unique_names(mock_cluster_state):
    plan = auto.build_auto_plan(topo_ns="eda")
    simulation_spec = parse_simulation_spec(plan.raw_spec)
    bundle = topology._render_bundle(simulation_spec, namespace="eda")

    simlink_names = [summary.name for summary in bundle.summaries if summary.kind == "SimLink"]
    topolink_names = [summary.name for summary in bundle.summaries if summary.kind == "TopoLink"]

    assert len(simlink_names) == len(set(simlink_names))
    assert len(topolink_names) == len(set(topolink_names))

    unique_pairs = {
        (att.fabric_node, att.fabric_interface, att.sim_node, att.sim_interface)
        for att in simulation_spec.attachments
    }
    assert len(topolink_names) == len(unique_pairs)


def test_bundle_contains_vlan_ip_configs(mock_cluster_state):
    plan = auto.build_auto_plan(topo_ns="eda")
    simulation_spec = parse_simulation_spec(plan.raw_spec)
    bundle = topology._render_bundle(simulation_spec, namespace="eda")

    vlan1001_entry = next(
        entry for entry in plan.raw_spec["topology"] if entry["simNode"] == "server1" and entry.get("vlan") == "1001"
    )
    vlan201_entry = next(
        entry for entry in plan.raw_spec["topology"] if entry["simNode"] == "server1" and entry.get("vlan") == "201"
    )

    config_map = {
        (config.name, config.vlan): config for config in bundle.node_configs
    }

    assert ("server1", "1001") in config_map
    vlan1001_config = config_map[("server1", "1001")]
    assert vlan1001_config.ip_address == vlan1001_entry["ipAddress"]
    assert vlan1001_config.gateway is None

    assert ("server1", "201") in config_map
    vlan201_config = config_map[("server1", "201")]
    assert vlan201_config.ip_address == vlan201_entry["ipAddress"]
    assert vlan201_config.gateway == vlan201_entry["gateway"]


def test_configure_linux_interfaces_adds_default_route(monkeypatch):
    commands = []

    def fake_run_command(cmd):
        commands.append(cmd)
        return ""

    monkeypatch.setattr(topology, "run_command", fake_run_command)

    config = NodeInterfaceConfig(
        name="server1",
        interface="eth1",
        ip_address="10.20.1.1/24",
        vlan="201",
        gateway="10.20.1.254",
    )

    _configure_linux_interfaces(
        core_ns="eda-system",
        configs=[config],
        pod_lookup={"server1": "pod-server1"},
        debug=False,
    )

    exec_commands = [cmd for cmd in commands if cmd and cmd[0] == "kubectl"]
    assert exec_commands, "expected kubectl exec command"
    assert "ip route replace default via 10.20.1.254 dev eth1.201" in exec_commands[0][-1]


def test_configure_linux_interfaces_without_gateway(monkeypatch):
    commands = []

    def fake_run_command(cmd):
        commands.append(cmd)
        return ""

    monkeypatch.setattr(topology, "run_command", fake_run_command)

    config = NodeInterfaceConfig(
        name="server1",
        interface="eth1",
        ip_address="172.19.233.10/24",
        vlan="1001",
        gateway=None,
    )

    _configure_linux_interfaces(
        core_ns="eda-system",
        configs=[config],
        pod_lookup={"server1": "pod-server1"},
        debug=False,
    )

    exec_commands = [cmd for cmd in commands if cmd and cmd[0] == "kubectl"]
    assert exec_commands, "expected kubectl exec command"
    assert "ip route replace default" not in exec_commands[0][-1]

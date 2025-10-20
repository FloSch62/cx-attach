# CX Attach Utility

Typer-based CLI that loads simple simulation definitions into the EDA toolbox, mirroring the behaviour of `cx/topology/topo.sh` but with a concise YAML format and `pyproject.toml` managed through `uv`.

## Prerequisites
- `uv` available in your PATH
- `kubectl` configured for the target cluster
- An active `eda-toolbox` pod in the namespace referenced by `CORE_NS` or `--core-namespace`

## Quick Start
```bash
cd cx-attach
uv run cx_attach apply --spec examples/demo_sim.yaml
```

The command snapshots existing `TopoNode`/`TopoLink` resources in the namespace before invoking `api-server-topo`, ensuring the fabric nodes remain untouched. Node names in your YAML must match the live fabric (the CLI validates this). If you need to push a fresh fabric topology definition, add `--topology <path-to-topology.yaml>`.

To clear the attachments and reset the ConfigMaps:
```bash
cd cx-attach
uv run cx_attach remove
```

Environment variables `TOPO_NS` and `CORE_NS` override the default namespaces (`eda` and `eda-system`). You can also pass `--topology-namespace` / `--core-namespace` options explicitly. The `remove` command only resets the `eda-topology-sim` ConfigMap and reuses the current `TopoNode` / `TopoLink` snapshot so fabric nodes persist.

## YAML Format
`examples/demo_sim.yaml` demonstrates the compact input supported by the CLI. Both the simple structure and the full `items/spec` EDA schema are accepted. Each topology entry must declare the fabric node/interface plus the container-side interface.

During `apply`, the CLI copies `topo.json` and `simtopo.json` into `/tmp/` on the toolbox pod and runs `api-server-topo` to activate the configuration. `remove` wipes the relevant ConfigMaps and triggers a refresh.

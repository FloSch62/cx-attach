# CX Attach Utility

<p align="center">
  Manage EDA simulation attachments safely from the command line.
</p>

Integrate container-based simulations with an existing [EDA CX fabric](https://docs.eda.dev) while keeping production TopoNodes and TopoLinks untouched. The CLI wraps the usual `kubectl` dance together with `api-server-topo` so you can stage or tear down simulations with a single command.

## Overview

Ad-hoc simulation endpoints are useful for testing, but pushing those changes manually is tedious and error-prone. `cx-attach` streamlines the workflow by:

1. Discovering the active `eda-toolbox` pod in the control namespace.
2. Collecting (or loading) the fabric topology JSON.
3. Validating and normalizing the simulation specification.
4. Copying both assets to the toolbox pod and invoking `api-server-topo` appropriately.

The result is a single Typer command that safely layers simulation state on top of the fabric configuration and rolls it back just as easily.

## Prerequisites

- **CLI runtime**
  - Python 3.11+ (handled automatically when using `uv`)
  - [uv](https://docs.astral.sh/uv) available on your `PATH`
- **Cluster access**
  - `kubectl` configured for the target cluster
  - Network reachability between your workstation and the Kubernetes API
- **EDA specifics**
  - At least one healthy `eda-toolbox` pod in the chosen core namespace
  - Existing fabric topology stored as `TopoNode`/`TopoLink` resources if you intend to reuse the live snapshot

## Installation

> [!TIP]
> **Why uv?** `uv` manages Python versions, virtual environments, and dependency resolution in one step—and is significantly faster than `pip`.

1. **Sync dependencies**
   ```bash
   uv sync
   ```
   This creates an isolated environment pinned to `pyproject.toml`.
2. **Activate the CLI**
   Use `uv run` to execute commands inside that environment without sourcing anything manually.

## Usage

Both `cx-attach` and `cx_attach` entry points are exposed. Prefix commands with `uv run` to avoid managing virtualenvs manually.

```bash
uv run cx_attach apply --spec examples/demo_sim.yaml
uv run cx_attach remove
```

### Common options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--spec`, `-s` | Yes | None | Path to the simulation YAML input |
| `--topology`, `-t` | No | None | Fabric topology YAML; omit to snapshot live TopoNode/TopoLink state |
| `--topology-namespace`, `-n` | No | `eda` | Namespace containing TopoNode/TopoLink ConfigMaps (`TOPO_NS`) |
| `--core-namespace`, `-c` | No | `eda-system` | Namespace hosting the `eda-toolbox` pod (`CORE_NS`) |

Environment variables `TOPO_NS` and `CORE_NS` provide the same overrides without using flags.

### `apply`

- Collects the fabric topology from the cluster when `--topology` is not provided.
- Validates the simulation spec against known fabric nodes to catch typos early.
- Copies `/tmp/topo.json` and `/tmp/simtopo.json` to the toolbox pod.
- Executes `api-server-topo -n <namespace> [-f topo.json] -s simtopo.json` to activate the configuration.

### `remove`

- Resets the `eda-topology-sim` ConfigMap by applying an empty payload.
- Refreshes `/tmp/topo.json` from the current TopoNode/TopoLink state.
- Triggers `api-server-topo -f /tmp/topo.json` to reapply the clean fabric configuration.

## Simulation spec format

The CLI accepts either the concise structure below or the full EDA `items/spec` schema. Keys `node` / `interface` refer to fabric elements, while `simNode` / `simNodeInterface` describe the container edge.

```yaml
simNodes:
  - name: edge-server
    image: ghcr.io/srl-labs/network-multitool:v0.4.1
    type: Linux
  - name: test-client
    image: ghcr.io/srl-labs/network-multitool:v0.4.1

topology:
  - node: leaf1
    interface: ethernet-1-1
    simNode: edge-server
    simNodeInterface: eth1
  - node: leaf2
    interface: ethernet-1-1
    simNode: test-client
    simNodeInterface: eth1
```

Validation ensures required keys exist and, when a fabric snapshot is available, that each `node` appears in the current topology.

## Typical workflow

1. Prepare or update your simulation YAML file.
2. Export `TOPO_NS`/`CORE_NS` or supply the matching flags.
3. Run `uv run cx_attach apply --spec path/to/sim.yaml` to stage the simulation.
4. Observe toolbox pod logs and ConfigMaps to confirm activation.
5. Run `uv run cx_attach remove` once the simulation should be torn down.

## Inspecting cluster state

```bash
kubectl -n "$TOPO_NS" get toponodes
kubectl -n "$TOPO_NS" get configmap eda-topology-sim -o yaml
kubectl -n "$CORE_NS" get pods -l eda.nokia.com/app=eda-toolbox
```

Need a refresher on the underlying resources? Consult the [EDA documentation](https://docs.eda.dev/).

## Development

```bash
uv run ruff check .
```

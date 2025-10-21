# CX Attach Utility

<p align="center">
  Render and manage EDA simulation CRDs with a single CLI.
</p>

`cx-attach` turns a compact simulation spec into the full set of
`SimNode`, `SimLink`, and edge `TopoLink` resources that EDA expects. When no
spec is provided, it derives the necessary attachments directly from
`VirtualNetwork` and `Interface` resources (macvrfs / ipvrfs, selectors,
auto-assigned IP pools). It renders the manifests locally, applies them
transactionally via the [ETC script](https://github.com/eda-labs/etc-script),
waits for the backing pods and finalises their VLAN/IP configuration. Teardown
follows the same path in reverse with `etc delete`.

## Overview

Running ad-hoc simulations usually means juggling YAML, `kubectl apply`, and a
few manual `exec` calls. This CLI streamlines the workflow:

1. Read the simplified spec (`simNodes`, `topology`) from disk.
2. Generate a multi-document YAML containing all required CRDs.
3. Call `etc apply` (dry-run first, then real apply) to create/update resources.
4. Wait for simulation pods (`cx-pod-name=<SimNode>`) to become Ready.
5. Configure VLAN/IP information inside each pod and optionally dump state for
   debugging.
6. Re-render the same bundle for `etc delete` when it is time to tear everything
   down.

## Prerequisites

- **CLI runtime**
  - Python 3.11+ (managed automatically when using `uv`).
  - [`uv`](https://docs.astral.sh/uv/) available on your `PATH`.
- **Cluster access**
  - `kubectl` configured for the target cluster and reachable API server.
- **EDA specifics**
  - The ETC helper installed locally:
    ```bash
    curl -o etc https://raw.githubusercontent.com/eda-labs/etc-script/refs/heads/main/etc.sh
    chmod +x etc
    sudo mv etc /usr/local/bin/etc
    ```
  - A healthy `eda-toolbox` pod in the control namespace handling `etc` calls.

## Installation

> [!TIP]
> **Why uv?** `uv` handles Python versions, dependency resolution, and
> virtualenv management in one concise command.

1. Synchronise dependencies:
   ```bash
   uv sync
   ```
2. Use `uv run` for all invocations so you never have to activate a venv
   manually.

## Usage

Both entry points `cx-attach` and `cx_attach` are exposed. Prefix each invocation
with `uv run`:

```bash
uv run cx_attach apply --debug                 # auto-generate from VirtualNetwork/Interface resources
uv run cx_attach apply --spec examples/demo_sim2.yaml
uv run cx_attach apply --spec examples/demo_sim2.yaml --emit-crds /tmp/sim.yaml
uv run cx_attach remove                       # auto-generate matching removal bundle
uv run cx_attach remove --spec examples/demo_sim2.yaml
```

### Common options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--spec`, `-s` | No | auto | Path to the simplified simulation spec; omit to synthesise from `VirtualNetwork` / `Interface` |
| `--emit-crds` | No | temp file | Persist the rendered CRDs to a path for inspection |
| `--topology-namespace`, `-n` | No | `eda` | Namespace for `SimNode`/`SimLink`/`TopoLink` resources (`TOPO_NS`) |
| `--core-namespace`, `-c` | No | `eda-system` | Namespace containing the simulation pods (`CORE_NS`) |
| `--debug` | No | `False` | Print the generated YAML, ETC dry-run output, and extra `kubectl` dumps |

The environment variables `TOPO_NS` and `CORE_NS` provide the same overrides as
the matching options.

### `apply`

1. Renders `SimNode`, `SimLink`, and `TopoLink` manifests into a single
   multi-document YAML (written to `/tmp/cx-attach-*.yaml` unless `--emit-crds`
   is provided).
2. Executes `etc apply -f <file> --dry-run` and aborts if validation fails.
3. Executes `etc apply -f <file>` and reports the created/updated resources.
4. Waits for every simulation pod and configures VLAN/IP data via `kubectl
   exec`.
5. When `--debug` is set, prints the manifest, ETC output, and
   `kubectl get simnodes/simlinks` snapshots.

### `remove`

1. Re-renders the same manifest from the spec.
2. Executes `etc delete -f <file> --dry-run` followed by the real delete.
3. Checks for leftover resources labelled `eda.nokia.com/simtopology=true` and
   warns if they remain.
4. The `--debug` flag triggers the same inspection helpers as `apply`.

## Simulation spec format

The CLI accepts either the concise structure below or the full `items/spec`
layout used by EDA. Keys `node`/`interface` describe the fabric side, while
`simNode`/`simNodeInterface` identify the simulation containers.

```yaml
simNodes:
  - name: edge-server
    image: ghcr.io/srl-labs/network-multitool:v0.4.1
    type: Linux
    ipAddress: 10.10.0.11/24
    vlan: 1001
  - name: test-client
    image: ghcr.io/srl-labs/network-multitool:v0.4.1
    type: Linux
    ipAddress: 10.10.0.12/24
    vlan: 1001

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

Per-node values such as `ipAddress` and `vlan` are consumed to configure the
pods after they start; the CRDs themselves stay minimal and contain only the
fields accepted by the official schema (`containerImage`, `operatingSystem`,
optionally `platform`, `version`, `dhcp`, …). Attachments may provide a `vlan`
override that is also applied during the post-configuration step. When no spec
is supplied, the CLI gathers VLAN selectors from `VirtualNetwork` resources,
matches them against `Interface` labels, and allocates IPs from the corresponding
IRB pools. Multiple VLANs per leaf interface are rendered as individual
`SimLink`/`TopoLink` documents referencing the same simulation node (e.g.
`server1`).

### Auto-generated specs

Running `cx_attach apply` without `--spec` inspects the target namespace for
`VirtualNetwork` custom resources and their referenced `Interface` objects:

- Each `interfaceSelector` (e.g. `eda.nokia.com/macvrf1001`) is matched against
  interface labels to find leaf-facing ports.
- When an IRB IP pool is available, the allocator hands out one address per
  leaf interface (skipping the gateway).
- Every unique fabric interface maps to a single simulation node (`server1`,
  `server2`, …), even if multiple VLANs land on it. The VLANs become separate
  `SimLink` / `TopoLink` attachments referencing the same node.

The auto-generated spec is fed straight into ETC; pass `--debug` to inspect the
rendered YAML or `--emit-crds` to persist it.

## Typical workflow

1. Edit the simulation spec (`examples/demo_sim2.yaml` is a good starting
   template).
2. Export `TOPO_NS`/`CORE_NS` (or pass the CLI options).
3. Run `uv run cx_attach apply --spec <file>` and monitor the ETC dry-run logs.
4. Use `kubectl get simnodes -n "$TOPO_NS" -o wide` to inspect the CRDs if
   needed.
5. Once the test is complete, run `uv run cx_attach remove --spec <file>` to
   delete the resources atomically.

## Inspecting cluster state

```bash
kubectl -n "$TOPO_NS" get simnodes -o wide
kubectl -n "$TOPO_NS" get simlinks -o yaml
kubectl -n "$TOPO_NS" get topolinks -l eda.nokia.com/simtopology=true -o yaml
kubectl -n "$CORE_NS" get pods -l cx-pod-name
```

Need a deeper explanation of the generated artefacts? Have a look at
`docs/demo_sim2_ablauf.md` for the end-to-end rundown.

## Development

```bash
uv run ruff check .
```

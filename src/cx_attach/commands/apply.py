"""Implementation of the `apply` command."""

from __future__ import annotations

import typer

from ..auto import build_auto_plan
from ..cli.options import (
    DEFAULT_CORE_NS,
    DEFAULT_TOPO_NS,
    CoreNamespaceOption,
    DebugOption,
    EmitCrdsOption,
    SpecOption,
    TopologyNamespaceOption,
)
from ..kubectl import CommandError
from ..topology import apply_simulation
from .utils import handle_cli_error


def apply_command(
    spec: SpecOption = None,
    topology_namespace: TopologyNamespaceOption = DEFAULT_TOPO_NS,
    core_namespace: CoreNamespaceOption = DEFAULT_CORE_NS,
    emit_crds: EmitCrdsOption = None,
    debug: DebugOption = False,
) -> None:
    """Materialise simulation resources via ETC."""

    try:
        raw_spec = None
        if spec is None:
            plan = build_auto_plan(topo_ns=topology_namespace)
            raw_spec = plan.raw_spec
            summary: dict[str, list[str]] = {}
            for attachment in plan.attachments:
                summary.setdefault(attachment.virtual_network, []).append(
                    attachment.sim_name
                )
            typer.echo("Synthesised simulation spec from VirtualNetwork resources:")
            for vn_name, nodes in sorted(summary.items()):
                typer.echo(f"  {vn_name}: {len(set(nodes))} simulation node(s)")
        else:
            plan = None
        apply_simulation(
            sim_spec_file=spec,
            raw_spec=raw_spec,
            topo_ns=topology_namespace,
            core_ns=core_namespace,
            emit_crds=emit_crds,
            debug=debug,
        )
    except (CommandError, ValueError, FileNotFoundError, RuntimeError) as exc:  # pragma: no cover
        handle_cli_error(exc)


__all__ = ["apply_command"]

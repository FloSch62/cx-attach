"""Implementation of the `remove` command."""

from __future__ import annotations

import typer

from ..auto import build_auto_plan
from ..cli.options import (
    DEFAULT_CORE_NS,
    DEFAULT_TOPO_NS,
    CoreNamespaceOption,
    DebugOption,
    SpecOption,
    TopologyNamespaceOption,
)
from ..kubectl import CommandError
from ..topology import remove_simulation
from .utils import handle_cli_error


def remove_command(
    spec: SpecOption = None,
    topology_namespace: TopologyNamespaceOption = DEFAULT_TOPO_NS,
    core_namespace: CoreNamespaceOption = DEFAULT_CORE_NS,
    debug: DebugOption = False,
) -> None:
    """Delete simulation resources via ETC."""

    try:
        raw_spec = None
        if spec is None:
            plan = build_auto_plan(topo_ns=topology_namespace)
            raw_spec = plan.raw_spec
            typer.echo(
                "Synthesised simulation spec from VirtualNetwork resources for removal"
            )
        else:
            plan = None
        remove_simulation(
            sim_spec_file=spec,
            raw_spec=raw_spec,
            topo_ns=topology_namespace,
            core_ns=core_namespace,
            debug=debug,
        )
    except (CommandError, ValueError, FileNotFoundError, RuntimeError) as exc:  # pragma: no cover
        handle_cli_error(exc)


__all__ = ["remove_command"]

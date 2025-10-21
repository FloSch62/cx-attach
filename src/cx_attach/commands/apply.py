"""Implementation of the `apply` command."""

from __future__ import annotations

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
    spec: SpecOption,
    topology_namespace: TopologyNamespaceOption = DEFAULT_TOPO_NS,
    core_namespace: CoreNamespaceOption = DEFAULT_CORE_NS,
    emit_crds: EmitCrdsOption = None,
    debug: DebugOption = False,
) -> None:
    """Materialise simulation resources via ETC."""

    try:
        apply_simulation(
            sim_spec_file=spec,
            topo_ns=topology_namespace,
            core_ns=core_namespace,
            emit_crds=emit_crds,
            debug=debug,
        )
    except (CommandError, ValueError, FileNotFoundError, RuntimeError) as exc:  # pragma: no cover
        handle_cli_error(exc)


__all__ = ["apply_command"]

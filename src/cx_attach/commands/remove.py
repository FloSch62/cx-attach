"""Implementation of the `remove` command."""

from __future__ import annotations

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
    spec: SpecOption,
    topology_namespace: TopologyNamespaceOption = DEFAULT_TOPO_NS,
    core_namespace: CoreNamespaceOption = DEFAULT_CORE_NS,
    debug: DebugOption = False,
) -> None:
    """Delete simulation resources via ETC."""

    try:
        remove_simulation(
            sim_spec_file=spec,
            topo_ns=topology_namespace,
            core_ns=core_namespace,
            debug=debug,
        )
    except (CommandError, ValueError, FileNotFoundError, RuntimeError) as exc:  # pragma: no cover
        handle_cli_error(exc)


__all__ = ["remove_command"]

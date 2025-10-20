"""Implementation of the `apply` command."""

from __future__ import annotations

from ..config import DEFAULT_CORE_NS, DEFAULT_TOPO_NS
from ..kubectl import CommandError
from ..options import (
    CoreNamespaceOption,
    SpecOption,
    TopologyNamespaceOption,
    TopologyOption,
)
from ..topology import apply_topology
from .utils import handle_cli_error


def apply_command(
    spec: SpecOption,
    topology: TopologyOption = None,
    topology_namespace: TopologyNamespaceOption = DEFAULT_TOPO_NS,
    core_namespace: CoreNamespaceOption = DEFAULT_CORE_NS,
) -> None:
    """Load topology plus simulation attachments defined in YAML."""

    try:
        apply_topology(
            topology_file=topology,
            sim_spec_file=spec,
            topo_ns=topology_namespace,
            core_ns=core_namespace,
        )
    except (CommandError, ValueError, FileNotFoundError) as exc:  # pragma: no cover
        handle_cli_error(exc)


__all__ = ["apply_command"]

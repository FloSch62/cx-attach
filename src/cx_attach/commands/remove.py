"""Implementation of the `remove` command."""

from __future__ import annotations

from ..cli.options import (
    DEFAULT_CORE_NS,
    DEFAULT_TOPO_NS,
    CoreNamespaceOption,
    TopologyNamespaceOption,
)
from ..kubectl import CommandError
from ..topology import remove_sim_spec
from .utils import handle_cli_error


def remove_command(
    topology_namespace: TopologyNamespaceOption = DEFAULT_TOPO_NS,
    core_namespace: CoreNamespaceOption = DEFAULT_CORE_NS,
) -> None:
    """Remove simulation attachments without altering the fabric topology."""

    try:
        remove_sim_spec(topo_ns=topology_namespace, core_ns=core_namespace)
    except (CommandError, ValueError, FileNotFoundError) as exc:  # pragma: no cover
        handle_cli_error(exc)


__all__ = ["remove_command"]

"""Shared Typer option definitions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

DEFAULT_TOPO_NS = os.environ.get("TOPO_NS", "eda")
DEFAULT_CORE_NS = os.environ.get("CORE_NS", "eda-system")

SpecOption = Annotated[
    Path,
    typer.Option(
        "--spec",
        "-s",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the simplified simulation spec YAML file",
    ),
]

TopologyNamespaceOption = Annotated[
    str,
    typer.Option(
        "--topology-namespace",
        "-n",
        envvar="TOPO_NS",
        help="Namespace containing the simulation CRDs.",
    ),
]

CoreNamespaceOption = Annotated[
    str,
    typer.Option(
        "--core-namespace",
        "-c",
        envvar="CORE_NS",
        help="Namespace hosting the eda-toolbox pod.",
    ),
]

EmitCrdsOption = Annotated[
    Path | None,
    typer.Option(
        "--emit-crds",
        file_okay=True,
        dir_okay=False,
        writable=True,
        resolve_path=True,
        help="Persist the generated CRDs to the provided path for inspection.",
    ),
]

DebugOption = Annotated[
    bool,
    typer.Option(
        "--debug",
        is_flag=True,
        help="Enable verbose output and dump debug information.",
    ),
]


__all__ = [
    "DEFAULT_CORE_NS",
    "DEFAULT_TOPO_NS",
    "CoreNamespaceOption",
    "DebugOption",
    "EmitCrdsOption",
    "SpecOption",
    "TopologyNamespaceOption",
]

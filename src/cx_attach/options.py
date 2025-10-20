"""Shared Typer option definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

TopologyOption = Annotated[
    Path | None,
    typer.Option(
        "--topology",
        "-t",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help=(
            "Optional path to the fabric topology YAML file; omit to leave existing "
            "fabric untouched."
        ),
    ),
]

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
        help="Namespace containing the EDA topology ConfigMaps.",
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


__all__ = [
    "CoreNamespaceOption",
    "SpecOption",
    "TopologyNamespaceOption",
    "TopologyOption",
]

"""Shared helpers for CLI command modules."""

from __future__ import annotations

import typer


def handle_cli_error(exc: Exception) -> None:
    """Display an error message and exit."""

    typer.secho(f"Error: {exc}", err=True, fg=typer.colors.RED)
    raise typer.Exit(1)


__all__ = ["handle_cli_error"]

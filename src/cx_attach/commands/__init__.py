"""Command registrations for cx-attach."""

from __future__ import annotations

from typer import Typer

from .apply import apply_command
from .remove import remove_command


def register(app: Typer) -> None:
    """Register CLI commands with the provided Typer app."""

    app.command("apply")(apply_command)
    app.command("remove")(remove_command)


__all__ = ["apply_command", "register", "remove_command"]

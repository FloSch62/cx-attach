"""Typer application entry point for cx-attach."""

from __future__ import annotations

import typer

from ..commands import register

app = typer.Typer(help="Manage edge simulation attachments for EDA topologies.")
register(app)

__all__ = ["app"]

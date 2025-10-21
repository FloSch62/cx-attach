"""Helpers for interacting with external commands."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable


class CommandError(RuntimeError):
    """Raised when an external command invocation fails."""


def _format_command(cmd: Iterable[str]) -> str:
    return " ".join(cmd)


def run_command(cmd: Iterable[str], *, input_text: str | None = None) -> str:
    """Run a command and return stdout, raising CommandError on failure."""

    command = tuple(cmd)
    try:
        process = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover
        raise CommandError(f"Executable not found for command: {_format_command(command)}") from exc

    if process.returncode != 0:
        raise CommandError(
            "\n".join(
                (
                    f"Command failed: {_format_command(command)}",
                    f"stdout: {process.stdout.rstrip()}" if process.stdout else "stdout: <empty>",
                    f"stderr: {process.stderr.rstrip()}" if process.stderr else "stderr: <empty>",
                )
            )
        )
    return process.stdout.rstrip()


__all__ = ["CommandError", "run_command"]

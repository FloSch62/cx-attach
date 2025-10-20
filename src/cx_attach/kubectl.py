"""Helpers for interacting with kubectl."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class CommandError(RuntimeError):
    """Raised when a kubectl invocation fails."""


def run_command(cmd: Iterable[str], *, input_text: str | None = None) -> str:
    """Run a command and return stdout, raising if it fails."""

    process = subprocess.run(
        list(cmd),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise CommandError(
            "\n".join(
                (
                    f"Command failed: {' '.join(cmd)}",
                    f"stdout: {process.stdout.strip()}",
                    f"stderr: {process.stderr.strip()}",
                )
            )
        )
    return process.stdout.strip()


def find_toolbox_pod(core_ns: str) -> str:
    jsonpath = "{.items[0].metadata.name}"
    cmd = (
        "kubectl",
        "-n",
        core_ns,
        "get",
        "pods",
        "-l",
        "eda.nokia.com/app=eda-toolbox",
        "-o",
        f"jsonpath={jsonpath}",
    )
    pod_name = run_command(cmd)
    if not pod_name:
        raise CommandError(f"No eda-toolbox pod found in namespace {core_ns}")
    return pod_name


def copy_to_toolbox(core_ns: str, toolbox_pod: str, local_path: Path, remote_name: str) -> None:
    cmd = (
        "kubectl",
        "-n",
        core_ns,
        "cp",
        str(local_path),
        f"{toolbox_pod}:/tmp/{remote_name}",
    )
    run_command(cmd)


def exec_in_toolbox(core_ns: str, toolbox_pod: str, command: Iterable[str]) -> None:
    cmd = (
        "kubectl",
        "-n",
        core_ns,
        "exec",
        toolbox_pod,
        "--",
        *command,
    )
    run_command(cmd)


def load_namespace_resource(namespace: str, resource: str) -> list[dict[str, Any]]:
    raw = run_command(
        (
            "kubectl",
            "-n",
            namespace,
            "get",
            resource,
            "-o",
            "json",
        )
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise ValueError(f"Failed to decode JSON from kubectl {resource}: {raw}") from exc

    items = payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError(f"Unexpected payload for {resource}: missing 'items' list")
    return items


__all__ = [
    "CommandError",
    "copy_to_toolbox",
    "exec_in_toolbox",
    "find_toolbox_pod",
    "load_namespace_resource",
    "run_command",
]

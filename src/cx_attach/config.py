"""Configuration defaults for cx-attach."""

from __future__ import annotations

import os

DEFAULT_TOPO_NS = os.environ.get("TOPO_NS", "eda")
DEFAULT_CORE_NS = os.environ.get("CORE_NS", "eda-system")

__all__ = ["DEFAULT_CORE_NS", "DEFAULT_TOPO_NS"]

"""Tool schemas: the bundled snapshot, the per-profile cache, and MCP to Hermes conversion."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

SNAPSHOT_FILE = Path(__file__).with_name("tools_snapshot.json")
CACHE_DIR = "lumberroom"
CACHE_NAME = "tools_cache.json"


def load_snapshot() -> dict[str, Any]:
    """{"handshake", "server_info", "instructions", "tools": [MCP tool dicts]} as L0 captured it."""
    raise NotImplementedError("T3")


def read_cache(hermes_home: str) -> dict[str, Any] | None:
    """The last live listing for this profile, same shape as the snapshot; None when absent or unreadable."""
    raise NotImplementedError("T3")


def write_cache(hermes_home: str, listing: Mapping[str, Any]) -> None:
    """Atomic write of {"instructions", "tools"} to $HERMES_HOME/lumberroom/tools_cache.json."""
    raise NotImplementedError("T3")


def to_hermes_schema(tool: Mapping[str, Any]) -> dict[str, Any]:
    """MCP {"name", "description", "inputSchema"} to Hermes {"name", "description", "parameters"}."""
    raise NotImplementedError("T3")


def select(allowlist: Sequence[str], tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The MCP tool dicts whose names the allowlist carries, in allowlist order."""
    raise NotImplementedError("T3")

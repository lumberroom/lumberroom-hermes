"""The plugin's own OAuth token file and the lock that lets one process refresh at a time."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

REFRESH_SKEW_S = 60.0     # design target: refresh this long before expiry
FENCE_TIMEOUT_S = 30.0    # design target


class FenceTimeout(Exception):
    """A peer held the refresh lock past the timeout."""


@dataclass(frozen=True)
class StoredOAuth:
    mcp_url: str
    tokens: dict[str, Any] | None           # OAuthToken dump
    expires_at: float | None                # absolute unix seconds; None counts as due for refresh
    client_info: dict[str, Any] | None      # OAuthClientInformationFull dump
    oauth_metadata: dict[str, Any] | None   # OAuthMetadata dump, token_endpoint included


def token_paths(hermes_home: str) -> tuple[Path, Path]:
    """($HERMES_HOME/lumberroom/oauth.json, $HERMES_HOME/lumberroom/oauth.lock)."""
    raise NotImplementedError("T2")


class FileTokenStorage:
    """mcp.client.auth TokenStorage over one 0600 JSON file bound to one mcp_url."""

    def __init__(self, path: Path, mcp_url: str, *, clock: Callable[[], float] = time.time) -> None:
        raise NotImplementedError("T2")

    async def get_tokens(self):
        raise NotImplementedError("T2")

    async def set_tokens(self, tokens) -> None:
        raise NotImplementedError("T2")

    async def get_client_info(self):
        raise NotImplementedError("T2")

    async def set_client_info(self, client_info) -> None:
        raise NotImplementedError("T2")

    def read(self) -> StoredOAuth:
        raise NotImplementedError("T2")

    def save_metadata(self, metadata: Mapping[str, Any]) -> None:
        raise NotImplementedError("T2")

    def mtime_ns(self) -> int:
        """0 when the file is absent."""
        raise NotImplementedError("T2")

    def clear(self) -> bool:
        raise NotImplementedError("T2")


class RefreshFence:
    """Cross-process exclusive lock on the lock file; acquired on a worker thread."""

    def __init__(self, lock_path: Path, *, timeout_s: float = FENCE_TIMEOUT_S) -> None:
        raise NotImplementedError("T2")

    async def __aenter__(self) -> "RefreshFence":
        raise NotImplementedError("T2")

    async def __aexit__(self, *exc: object) -> None:
        raise NotImplementedError("T2")

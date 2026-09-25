"""The plugin's own OAuth token file and the lock that lets one process refresh at a time."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import filelock

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
    base = Path(hermes_home) / "lumberroom"
    return base / "oauth.json", base / "oauth.lock"


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(by_alias=True, mode="json", exclude_none=True)


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


class FileTokenStorage:
    """mcp.client.auth TokenStorage over one 0600 JSON file bound to one mcp_url."""

    def __init__(self, path: Path, mcp_url: str, *, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._mcp_url = mcp_url
        self._clock = clock
        # The SDK and save_metadata can write from different threads; each write re-reads first.
        self._write_lock = threading.Lock()

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        from pydantic import ValidationError

        raw = self.read().tokens
        if raw is None:
            return None
        try:
            return OAuthToken.model_validate(raw)
        except ValidationError:
            return None

    async def set_tokens(self, tokens) -> None:
        # A missing expires_in stays null on disk so refresh_guard treats the pair as due.
        expires_at = None if tokens.expires_in is None else self._clock() + float(tokens.expires_in)
        self.save_tokens(_dump(tokens), expires_at)

    def save_tokens(self, tokens: Mapping[str, Any], expires_at: float | None, *,
                    client_info: Mapping[str, Any] | None = None,
                    oauth_metadata: Mapping[str, Any] | None = None) -> None:
        """Write a token dump with an absolute expiry, for a pair whose first write failed.

        client_info and oauth_metadata restore what an unlinked file lost; None keeps what is on disk.
        """
        fields: dict[str, Any] = {"tokens": dict(tokens), "expires_at": expires_at}
        if client_info is not None:
            fields["client_info"] = dict(client_info)
        if oauth_metadata is not None:
            fields["oauth_metadata"] = dict(oauth_metadata)
        self._update(**fields)

    def drop_refresh_token(self) -> None:
        """Keep the access token and forget the refresh token. No file, no write.

        For a refresh whose answer never came back: the engine may have spent the token already,
        and presenting it again would revoke the whole family.
        """
        with self._write_lock:
            current = self.read()
            if current.tokens is None:
                return
            tokens = {k: v for k, v in current.tokens.items() if k != "refresh_token"}
            self._write({"mcp_url": self._mcp_url, "tokens": tokens, "expires_at": current.expires_at,
                         "client_info": current.client_info, "oauth_metadata": current.oauth_metadata})

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import ValidationError

        raw = self.read().client_info
        if raw is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except ValidationError:
            return None

    async def set_client_info(self, client_info) -> None:
        self._update(client_info=_dump(client_info))

    def read(self) -> StoredOAuth:
        empty = StoredOAuth(self._mcp_url, None, None, None, None)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        # A file for another server reads as empty, so a changed base_url never sends one
        # server's token to another. The file stays on disk for the user to inspect.
        if not isinstance(data, dict) or data.get("mcp_url") != self._mcp_url:
            return empty
        expires_at = data.get("expires_at")
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
            expires_at = None
        return StoredOAuth(
            mcp_url=self._mcp_url,
            tokens=_dict_or_none(data.get("tokens")),
            expires_at=None if expires_at is None else float(expires_at),
            client_info=_dict_or_none(data.get("client_info")),
            oauth_metadata=_dict_or_none(data.get("oauth_metadata")),
        )

    def save_metadata(self, metadata: Mapping[str, Any]) -> None:
        self._update(oauth_metadata=dict(metadata))

    def mtime_ns(self) -> int:
        """0 when the file is absent."""
        try:
            return os.stat(self._path).st_mtime_ns
        except FileNotFoundError:
            return 0

    def clear(self) -> bool:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _update(self, **fields: Any) -> None:
        with self._write_lock:
            current = self.read()
            record = {
                "mcp_url": self._mcp_url,
                "tokens": current.tokens,
                "expires_at": current.expires_at,
                "client_info": current.client_info,
                "oauth_metadata": current.oauth_metadata,
            }
            record.update(fields)
            self._write(record)

    def _write(self, record: dict[str, Any]) -> None:
        # Temp file in the same directory, then rename: a reader sees the old file or the new
        # one, never half of either, and a crash mid-write leaves the old pair usable.
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".oauth-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise


class RefreshFence:
    """Cross-process exclusive lock on the lock file; acquired on a worker thread."""

    def __init__(self, lock_path: Path, *, timeout_s: float = FENCE_TIMEOUT_S) -> None:
        self._lock_path = Path(lock_path)
        # thread_local=False because a worker thread acquires and the loop thread releases.
        # Under filelock's default the release finds no hold on its own thread and does nothing.
        self._lock = filelock.FileLock(str(self._lock_path), timeout=timeout_s, mode=0o600,
                                       thread_local=False)

    async def __aenter__(self) -> "RefreshFence":
        self._lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        task = asyncio.ensure_future(asyncio.to_thread(self._acquire))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Nobody can interrupt the worker thread. If it wins the lock after the caller gave
            # up, release it there, or the lock stays held until this process exits.
            task.add_done_callback(self._release_abandoned)
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._lock.release()

    def __enter__(self) -> "RefreshFence":
        # For callers with no loop, such as logout. Blocks the calling thread up to the timeout.
        self._lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self._lock.release()

    def _acquire(self) -> None:
        try:
            self._lock.acquire()
        except filelock.Timeout as e:
            raise FenceTimeout(f"another process held {self._lock_path} past the timeout") from e

    def _release_abandoned(self, task: asyncio.Future) -> None:
        if not task.cancelled() and task.exception() is None:
            self._lock.release()

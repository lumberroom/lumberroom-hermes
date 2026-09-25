"""Static bearer or OAuth through the mcp SDK, behind one handle the bridge uses."""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Protocol

from .config import LumberroomConfig

CLIENT_NAME = "Hermes Agent (lumberroom)"


class LoginRequired(Exception):
    """OAuth needs a browser and this process may not open one."""


class AuthConfigError(Exception):
    """The credential the config names is missing."""


class LoginFailed(Exception):
    """The authorization server or the pasted URL refused the login."""


class AuthHandle(Protocol):
    mode: str

    def headers(self) -> dict[str, str]: ...

    def httpx_auth(self) -> Any: ...   # httpx2.Auth or None

    def refresh_guard(self) -> contextlib.AbstractAsyncContextManager[None]: ...


def token_present(cfg: LumberroomConfig) -> bool:
    """Token mode: LUMBERROOM_HERMES_TOKEN is non-empty. OAuth mode: True. No network, no file I/O."""
    raise NotImplementedError("T2")


def build_auth(cfg: LumberroomConfig, *, hermes_home: str, interactive: bool = False,
               open_browser: bool = True, read_pasted: Callable[[], str] | None = None,
               out: Callable[[str], None] = print) -> AuthHandle:
    """Token mode reads the secret now, on the calling thread. Non-interactive OAuth handlers raise LoginRequired."""
    raise NotImplementedError("T2")


def parse_callback(url: str) -> tuple[str, str | None]:
    """(code, state) from a pasted redirect URL. Raises LoginFailed on error= or a missing code."""
    raise NotImplementedError("T2")


def login(cfg: LumberroomConfig, *, hermes_home: str, open_browser: bool,
          read_pasted: Callable[[], str], out: Callable[[str], None] = print) -> None:
    """Run the OAuth flow to a stored token pair. Prints the authorize URL on its own line."""
    raise NotImplementedError("T2")


def logout(cfg: LumberroomConfig, *, hermes_home: str) -> bool:
    """Delete this profile's token file. True when one existed."""
    raise NotImplementedError("T2")

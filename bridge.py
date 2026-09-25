"""One loop thread and two MCP sessions to the engine: hook calls and model calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Sequence

from .auth import AuthHandle
from .config import LumberroomConfig

Invocation = Literal["hook", "model"]
CallKind = Literal["ok", "tool_error", "unreachable", "timeout", "unauthorized", "login_required"]
CLIENT_INFO_NAME = "lumberroom-hermes"


@dataclass(frozen=True)
class CallResult:
    kind: CallKind
    structured: dict[str, Any] | None
    text: str
    error: str | None

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


@dataclass(frozen=True)
class ToolsListing:
    tools: tuple[dict[str, Any], ...]   # MCP tool dicts, camelCase keys
    instructions: str | None


class Bridge:
    def __init__(self, cfg: LumberroomConfig, auth: AuthHandle, *, session_id: str,
                 client_factory: Callable[..., Any] | None = None) -> None:
        """client_factory(**httpx2.AsyncClient kwargs) -> httpx2.AsyncClient; tests pass the fake's."""
        raise NotImplementedError("T1")

    def start(self) -> None:
        raise NotImplementedError("T1")

    def list_tools(self, *, timeout: float) -> tuple[CallResult, ToolsListing | None]:
        raise NotImplementedError("T1")

    def call(self, tool: str, args: dict[str, Any], *, invocation: Invocation, timeout: float) -> CallResult:
        raise NotImplementedError("T1")

    def call_many(self, calls: Sequence[tuple[str, dict[str, Any]]], *, invocation: Invocation,
                  timeout: float) -> list[CallResult]:
        """Concurrent calls under one deadline, results in input order."""
        raise NotImplementedError("T1")

    def http_json(self, method: str, path: str, body: dict[str, Any] | None, *,
                  timeout: float) -> tuple[int, Any]:
        """A plain request to the engine origin with the same auth. (status, parsed JSON or None)."""
        raise NotImplementedError("T1")

    def set_session_id(self, session_id: str) -> None:
        raise NotImplementedError("T1")

    def close(self, *, timeout: float = 2.0) -> None:
        raise NotImplementedError("T1")

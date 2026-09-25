"""The Hermes MemoryProvider. Hooks only: I/O lives in the bridge, formatting in recall."""

from __future__ import annotations

import time
from typing import Any, Callable

from agent.memory_provider import MemoryProvider, RecallStatus

from .auth import AuthHandle
from .bridge import Bridge
from .config import LumberroomConfig

BridgeFactory = Callable[[LumberroomConfig, AuthHandle, str], Bridge]
AuthFactory = Callable[[LumberroomConfig, str], AuthHandle]


class LumberroomProvider(MemoryProvider):
    def __init__(self, *, bridge_factory: BridgeFactory | None = None,
                 auth_factory: AuthFactory | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        raise NotImplementedError("T4")

    @property
    def name(self) -> str:
        return "lumberroom"

    def is_available(self) -> bool: raise NotImplementedError("T4")
    def unavailable_reason(self) -> str: raise NotImplementedError("T4")
    def initialize(self, session_id: str, **kwargs: Any) -> None: raise NotImplementedError("T4")
    def system_prompt_block(self) -> str: raise NotImplementedError("T4")
    def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None: raise NotImplementedError("T4")
    def prefetch(self, query: str, *, session_id: str = "") -> str: raise NotImplementedError("T4")
    def recall_status(self) -> RecallStatus | None: raise NotImplementedError("T4")
    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: list[dict[str, Any]] | None = None,
                  turn_author: dict[str, Any] | None = None) -> None: raise NotImplementedError("T4")
    def get_tool_schemas(self) -> list[dict[str, Any]]: raise NotImplementedError("T4")
    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str: raise NotImplementedError("T4")
    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False,
                          rewound: bool = False, **kwargs: Any) -> None: raise NotImplementedError("T4")
    def shutdown(self) -> None: raise NotImplementedError("T4")
    def get_config_schema(self) -> list[dict[str, Any]]: raise NotImplementedError("T4")
    def save_config(self, values: dict[str, Any], hermes_home: str) -> None: raise NotImplementedError("T4")
    def post_setup(self, hermes_home: str, config: dict[str, Any]) -> None: raise NotImplementedError("T4")
    def get_status_config(self, provider_config: dict[str, Any]) -> dict[str, Any]: raise NotImplementedError("T4")

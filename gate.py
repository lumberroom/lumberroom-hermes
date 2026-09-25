"""Who may reach the owner's memory from this session and this turn.

Fails closed where it cannot tell who is speaking: an empty owner_user_ids refuses every gateway
session, and a shared-chat turn with no author is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .config import LumberroomConfig

REFUSAL = "lumberroom tools are limited to the owner in this chat."


@dataclass(frozen=True)
class SessionIdentity:
    platform: str
    user_id: str | None
    chat_type: str | None
    agent_context: str


def identity_from_kwargs(kwargs: Mapping[str, Any]) -> SessionIdentity:
    """From initialize kwargs. platform defaults to "cli", agent_context to "primary"; ids become str."""
    raise NotImplementedError("T3")


def session_allowed(ident: SessionIdentity, cfg: LumberroomConfig) -> bool:
    """Local platform: allowed. cron outside local_platforms, or any gateway with no owner_user_ids:
    refused. A DM: allowed when platform:user_id is listed. Any other chat type, or none: allowed,
    and turn_allowed decides each turn."""
    raise NotImplementedError("T3")


def turn_allowed(ident: SessionIdentity, author_id: str | None, cfg: LumberroomConfig) -> bool:
    """session_allowed first. Local platform: allowed. A DM: refused only for a present author_id
    that is not listed. Any other chat: allowed only for a present author_id whose
    platform:author_id is listed."""
    raise NotImplementedError("T3")

"""What prefetch injects: the digest, the hits, and the fixed status lines. Pure functions."""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Mapping, Sequence

DIGEST_HEADING = "## lumberroom: what is already known"
HITS_HEADING = "## lumberroom: relevant to this message"
UNREACHABLE_LINE = "lumberroom unreachable: memory was not checked this turn."
LOGIN_LINE = "lumberroom is not logged in, so memory was not checked. Run `hermes lumberroom login`."
NUDGE_LINE = (
    "Review the recent turns. Write each decision, preference, constraint or durable fact not yet "
    "stored with memory_write, one fact per call."
)
# Design targets. The whole block stays under Hermes's 10,000-character spill threshold.
PREFETCH_MAX_CHARS = 8000
QUERY_MAX_CHARS = 1000


def clip_query(query: str) -> str:
    raise NotImplementedError("T3")


def format_digest(text: str, max_chars: int) -> str:
    """Heading plus text cut at the last newline within max_chars; "" for empty text or max_chars 0."""
    raise NotImplementedError("T3")


def format_hit(hit: Mapping[str, Any]) -> str:
    """One bullet: "- [ns] content (id <uuid>, source <source>, occurred <YYYY-MM-DD>)"."""
    raise NotImplementedError("T3")


class InjectedIds:
    def __init__(self) -> None:
        raise NotImplementedError("T3")

    def __contains__(self, memory_id: object) -> bool:
        raise NotImplementedError("T3")

    def add(self, ids: Iterable[str]) -> None:
        raise NotImplementedError("T3")

    def clear(self) -> None:
        raise NotImplementedError("T3")


def format_hits(hits: Sequence[Mapping[str, Any]], seen: InjectedIds, max_chars: int) -> tuple[str, list[str]]:
    """(block with heading, ids included). Skips ids in seen, stops before max_chars. ("", []) when none."""
    raise NotImplementedError("T3")


def compose(parts: Sequence[str], max_chars: int = PREFETCH_MAX_CHARS) -> str:
    """Join non-empty parts with a blank line, dropping trailing parts that would pass max_chars."""
    raise NotImplementedError("T3")


class Breaker:
    """Opens after `threshold` consecutive failures, for `cooldown_s`. Design targets 3 and 60."""

    def __init__(self, *, threshold: int = 3, cooldown_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        raise NotImplementedError("T3")

    def allow(self) -> bool:
        raise NotImplementedError("T3")

    def record_success(self) -> None:
        raise NotImplementedError("T3")

    def record_failure(self) -> bool:
        """True when this failure starts an outage, so the caller says so once."""
        raise NotImplementedError("T3")

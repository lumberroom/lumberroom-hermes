"""What prefetch injects: the digest, the hits, and the fixed status lines. Pure functions."""

from __future__ import annotations

import re
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
    return query[:QUERY_MAX_CHARS]


def format_digest(text: str, max_chars: int) -> str:
    """Heading plus text cut at the last newline within max_chars; "" for empty text or max_chars 0."""
    if not text or max_chars <= 0:
        return ""
    budget = max_chars - len(DIGEST_HEADING) - 1
    if budget <= 0:
        return ""
    body = text if len(text) <= budget else text[:budget].rsplit("\n", 1)[0]
    if not body:
        return ""
    return f"{DIGEST_HEADING}\n{body}"


_FENCE_TAG = re.compile(r"</?\s*memory-context\s*>", re.IGNORECASE)


def format_hit(hit: Mapping[str, Any]) -> str:
    """One bullet: "- [ns] content (id <uuid>, source <source>, occurred <YYYY-MM-DD>)"."""
    content = _FENCE_TAG.sub("", str(hit.get("content", "")))
    content = " ".join(content.split())
    meta = [f"id {hit['id']}"]
    source = hit.get("source")
    if source:
        meta.append(f"source {source}")
    occurred_at = hit.get("occurred_at")
    if occurred_at:
        meta.append(f"occurred {str(occurred_at)[:10]}")
    return f"- [{hit.get('namespace')}] {content} ({', '.join(meta)})"


class InjectedIds:
    def __init__(self) -> None:
        self._ids: set[str] = set()

    def __contains__(self, memory_id: object) -> bool:
        return memory_id in self._ids

    def add(self, ids: Iterable[str]) -> None:
        self._ids.update(ids)

    def clear(self) -> None:
        self._ids.clear()


def format_hits(hits: Sequence[Mapping[str, Any]], seen: InjectedIds, max_chars: int) -> tuple[str, list[str]]:
    """(block with heading, ids included). Skips ids in seen, stops before max_chars. ("", []) when none."""
    lines: list[str] = []
    ids: list[str] = []
    total = len(HITS_HEADING)
    for hit in hits:
        hit_id = hit.get("id")
        if hit_id in seen:
            continue
        line = format_hit(hit)
        added = len(line) + 1
        if total + added > max_chars:
            break
        lines.append(line)
        ids.append(hit_id)
        total += added
    if not lines:
        return "", []
    return HITS_HEADING + "\n" + "\n".join(lines), ids


def compose(parts: Sequence[str], max_chars: int = PREFETCH_MAX_CHARS) -> str:
    """Join non-empty parts with a blank line, dropping trailing parts that would pass max_chars."""
    kept: list[str] = []
    total = 0
    for part in parts:
        if not part:
            continue
        added = len(part) if not kept else len(part) + 2
        if total + added > max_chars:
            break
        kept.append(part)
        total += added
    return "\n\n".join(kept)


class Breaker:
    """Opens after `threshold` consecutive failures, for `cooldown_s`. Design targets 3 and 60."""

    def __init__(self, *, threshold: int = 3, cooldown_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at >= self._cooldown_s:
            self._opened_at = None
            self._consecutive_failures = 0
            return True
        return False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> bool:
        """True when this failure starts an outage, so the caller says so once."""
        starts_outage = self._consecutive_failures == 0
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold and self._opened_at is None:
            self._opened_at = self._clock()
        return starts_outage

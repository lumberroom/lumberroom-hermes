"""Send Hermes's MEMORY.md and USER.md entries to the engine's proposal queue, never the store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .bridge import Bridge

ENTRY_DELIMITER = "\n§\n"   # hermes-agent tools/memory_tool_store.py:23
EXTRACTOR = "hermes-builtin-import"
SPEAKER = "main_model"      # never auto-approves: ENG/src/services/ingest.rs:97-106


class MissingGrant(Exception):
    """The credential lacks mayIngest (403)."""


@dataclass(frozen=True)
class BuiltinEntry:
    file: Literal["MEMORY.md", "USER.md"]
    path: str
    text: str
    sha256: str
    namespace: str


@dataclass(frozen=True)
class ImportReport:
    run_id: str | None
    posted: int
    proposals_new: int
    proposals_reinforced: int
    refused: int
    blocked: int


def read_builtin_entries(hermes_home: str) -> list[BuiltinEntry]:
    raise NotImplementedError("T5")


def proposals_body(entries: list[BuiltinEntry], run_id: str) -> dict[str, Any]:
    raise NotImplementedError("T5")


def import_builtin(bridge: Bridge, hermes_home: str, *, profile: str, dry_run: bool,
                   timeout: float = 20.0) -> ImportReport:
    raise NotImplementedError("T5")

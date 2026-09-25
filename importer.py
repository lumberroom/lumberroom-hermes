"""Send Hermes's MEMORY.md and USER.md entries to the engine's proposal queue, never the store."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .bridge import Bridge

ENTRY_DELIMITER = "\n§\n"   # hermes-agent tools/memory_tool_store.py:23
EXTRACTOR = "hermes-builtin-import"
SPEAKER = "main_model"      # never auto-approves: ENG/src/services/ingest.rs:97-106
BATCH_SIZE = 100
_FILES: tuple[tuple[Literal["MEMORY.md", "USER.md"], str], ...] = (
    ("MEMORY.md", "global"),
    ("USER.md", "user:me"),
)


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
    entries: list[BuiltinEntry] = []
    for filename, namespace in _FILES:
        path = Path(hermes_home) / "memories" / filename
        try:
            raw = path.read_text()
        except FileNotFoundError:
            continue
        for chunk in raw.split(ENTRY_DELIMITER):
            text = chunk.strip()
            if not text:
                continue
            entries.append(BuiltinEntry(file=filename, path=str(path), text=text,
                                         sha256=hashlib.sha256(text.encode()).hexdigest(), namespace=namespace))
    return entries


def proposals_body(entries: list[BuiltinEntry], run_id: str) -> dict[str, Any]:
    return {
        "extractor": EXTRACTOR,
        "facts": [
            {
                "content": e.text,
                "namespace": e.namespace,
                "tags": ["hermes-import"],
                "speaker": SPEAKER,
                "span_text": e.text,
                "source": {"file_path": e.path, "entry_uuid": e.sha256, "run_id": run_id},
            }
            for e in entries
        ],
    }


def _raise_on_grant_failure(status: int, payload: Any) -> None:
    if status == 403:
        raise MissingGrant()
    if status != 200:
        raise RuntimeError(f"lumberroom ingest call failed ({status}): {payload}")


def import_builtin(bridge: Bridge, hermes_home: str, *, profile: str, dry_run: bool,
                   timeout: float = 20.0) -> ImportReport:
    entries = read_builtin_entries(hermes_home)
    if dry_run or not entries:
        return ImportReport(run_id=None, posted=0, proposals_new=0, proposals_reinforced=0, refused=0, blocked=0)

    status, payload = bridge.http_json(
        "POST", "/admin/ingest/runs",
        {"extractor": EXTRACTOR, "scope": {"profile": profile, "hermes_home": hermes_home}}, timeout=timeout)
    _raise_on_grant_failure(status, payload)
    run_id = payload["run_id"]

    posted = proposals_new = proposals_reinforced = refused = blocked = 0
    for start in range(0, len(entries), BATCH_SIZE):
        batch = entries[start:start + BATCH_SIZE]
        status, payload = bridge.http_json("POST", "/admin/ingest/proposals", proposals_body(batch, run_id),
                                            timeout=timeout)
        _raise_on_grant_failure(status, payload)
        posted += len(batch)
        proposals_new += payload.get("proposals_new", 0)
        proposals_reinforced += payload.get("proposals_reinforced", 0)
        refused += payload.get("refused", 0)
        blocked += payload.get("blocked", 0)

    status, payload = bridge.http_json(
        "POST", f"/admin/ingest/runs/{run_id}/close",
        {"entries_seen": len(entries), "proposals_new": proposals_new, "proposals_reinforced": proposals_reinforced},
        timeout=timeout)
    _raise_on_grant_failure(status, payload)

    return ImportReport(run_id=run_id, posted=posted, proposals_new=proposals_new,
                         proposals_reinforced=proposals_reinforced, refused=refused, blocked=blocked)

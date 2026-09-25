# The Hermes memory provider, the order of work

> Written when the plugin lived at `client/hermes/` in the engine repository
> (`github.com/lumberroom/lumberroom`). It moved to `github.com/lumberroom/lumberroom-hermes` on
> 25 September 2026 for its own release cycle; `client/hermes/X` in this document is `X` at the root
> of this repository, and `ENG/` paths point into the engine.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. The lead
> orchestrates; subagents implement under absolute file ownership. Steps use `- [ ]` for tracking.

**Goal:** a Python package at `client/hermes/` that makes lumberroom Hermes Agent's memory provider:
recall on every turn, the engine's own tools for writes, one durable store, same code for a
self-hosted engine and lumberroom.cloud.

**Architecture:** a `MemoryProvider` whose hooks call a `Bridge`. The bridge owns one asyncio loop
thread and two MCP sessions (hook and model) against `/mcp`, through the official `mcp` SDK. Auth is a
static bearer or the SDK's `OAuthClientProvider` with a plugin-owned token file and a cross-process
lock. Formatting, gating and schema selection are pure modules the provider composes.

**Tech stack:** Python 3.14 in development (Hermes's supported version), `requires-python >=3.11`;
`mcp>=2.0.0,<3`, `httpx2>=2.7.0,<3`, `filelock>=3.12,<4`; pytest; hatchling for the wheel; bash for
the gate script, matching `scripts/oauth-flow-test.sh`.

**Spec:** [`hermes-plugin.md`](hermes-plugin.md). Decision: [`../decisions/0021-harness-plugins-talk-mcp.md`](../decisions/0021-harness-plugins-talk-mcp.md).
Executors read the spec first. Section numbers below (`spec §6.2`) point into it.

Nothing in this plan has run. Every "PASS" below is the gate's expected output, not an observation.

## Global constraints

- No em dashes in any file. `grep -rP '\x{2014}' <file>` prints nothing.
- No AI attribution anywhere: code, comments, docstrings, test names, commit messages.
- Subagents never run git, cargo or docker. They may run pytest on their own test files with the
  plugin venv, `.venv-hermes-plugin/bin/python`. The lead runs every gate script and commits.
- File ownership is absolute. A task that needs a change outside its list returns it under
  `wire_in` as an exact diff.
- The lock (L1) is the whole contract. No task adds a public name, a config key, a CLI flag, a
  dependency or a file. A task that finds one missing stops and returns it under `wire_in`.
- Relative imports only inside `client/hermes/`. Hermes loads the directory under a synthetic
  package name. `cli.py` imports nothing heavy at module level.
- Every Hermes import is one of: `agent.memory_provider`, `agent.secret_scope`, `hermes_cli.config`,
  `hermes_constants`, and in tests `agent.memory_manager` and `plugins.memory`.
- Secrets through `agent.secret_scope.get_secret`, read in the caller's context, never on the loop
  thread. Background threads through `agent.memory_provider.spawn_context_thread`.
- Wire names are the engine's, verbatim: tool names, argument names, `structuredContent` fields,
  `x-memory-invocation`, `x-session-id`, the ingest JSON. A paraphrased field is a runtime failure.
- Every number in user-facing text or a docstring is a design target unless it cites a measurement.
- Never write "verified", "works" or "tested" about code that has not run. Write "implemented" and
  name the gate.
- Nothing bills the owner: no model calls, no paid API, no CI minutes, no publishing. PyPI and the
  Hermes catalog PR stay with the owner.

## Review focus

Five inputs the spec implies and a happy-path test would miss, most likely first. Each has a test in
the task named.

1. **A multiplexed gateway reads the secret off-scope.** `get_secret` raises
   `UnscopedSecretError` when called with multiplexing on and no scope (`agent/secret_scope.py:200-228`).
   Expect the token read in `initialize`, on the caller's thread. Test: T2
   `test_token_auth_reads_the_secret_once_on_the_calling_thread`, T4
   `test_initialize_reads_the_secret_before_the_loop_thread_starts`.
2. **The engine restarts between two calls.** A redeploy drops every TCP connection. Expect the next
   call to reconnect and succeed, and a write that failed mid-flight to say so. Test: T1
   `test_a_session_that_lost_its_connection_reconnects_on_the_next_call`; the gate restarts the
   container between write and recall.
3. **`base_url` typed any of five ways.** `https://host`, `https://host/`, `https://host/mcp`,
   `https://host/mcp/`, `HTTPS://Host`. Expect one `mcp_url`. A bare `host:8787` is a `ConfigError`.
   Test: L1 `test_config.py`.
4. **A stored memory that contains a fence tag.** A fact holding `</memory-context>` would make the
   host strip it and log "provider returned pre-wrapped context" (`agent/memory_manager.py:321-325`).
   Expect the plugin to remove fence tags from hit content before formatting. Test: T3
   `test_a_hit_carrying_a_fence_tag_loses_the_tag`.
5. **A token file from another server or a crashed write.** Expect "logged out", the file left in
   place, no exception out of any hook. Test: T2 `test_a_file_for_another_server_reads_as_empty`,
   `test_a_corrupt_file_reads_as_logged_out_and_stays_on_disk`.

## CONTEXT block, pasted into every subagent prompt

```
You are building one part of the lumberroom memory provider for Hermes Agent, in the engine repo
at /Users/aditya/work/cbrspn-tech/lumberroom/.claude/worktrees/hermes-plugin, package directory
client/hermes/. Read docs/specs/hermes-plugin.md first, then your task in
docs/specs/hermes-plugin-plan.md. The lock (task L1) is committed: every public name, dataclass,
config key and dependency already exists as a stub. Implement against it; rename nothing.

Hard rules:
- Touch only the files your task owns. Need a change elsewhere, or a name the lock lacks? Stop and
  return it under wire_in as an exact diff.
- Never run git, cargo or docker. Never commit. Never publish anything. Take no action that can bill
  the owner: no model API calls, no paid services.
- Run your own tests only: cd client/hermes && ../../.venv-hermes-plugin/bin/python -m pytest
  tests/<your files> -q. Paste the summary line. Actually run them; an unrun test is not a result.
- Relative imports only inside client/hermes/. The only Hermes modules you may import are
  agent.memory_provider, agent.secret_scope, hermes_cli.config, hermes_constants, and in tests
  agent.memory_manager and plugins.memory. Hermes source for reference:
  /Users/aditya/work/open-source/hermes-agent at fdec926e.
- Read secrets with agent.secret_scope.get_secret on the caller's thread. Start threads with
  agent.memory_provider.spawn_context_thread.
- Lumberroom is the sole durable store. The plugin never stores a turn and never buffers a write.

Ground truth, verified against source on 25 September 2026:
- Hermes builds its tool routing table from get_tool_schemas() BEFORE initialize()
  (agent/memory_manager.py:386-430). After init, never expose a name the pre-init call lacked.
- Hermes joins prefetch on a thread for 8.0s, fences the result in <memory-context> itself, and
  swallows every hook exception. Return plain markdown. Never write <memory-context>.
- Engine tools and arguments: context_bootstrap(project?), memory_search(query, namespaces?,
  limit?, project?, include_superseded?, as_of?, tags?), memory_write(content, namespace, tags?,
  supersedes?, sensitivity?, occurred_at?), registry_get(kind, key, namespace?, project?),
  memory_forget(id, reason, dry_run?).
- memory_search structuredContent: {"namespaces": [..], "also_searched": [..], "hits": [{"id",
  "namespace", "content", "tags", "source", "sensitivity", "created_at", "score", "similarity",
  "primary", "superseded_by"?, "occurred_at"?, "occurred_until"?}]}. The writer is "source", not
  "source_client".
- context_bootstrap structuredContent carries "text", the rendered digest.
- memory_write structuredContent: {"id", "namespace", "sensitivity", "deduplicated",
  "superseded"?, "end_left_open"?, "possible_conflicts"?: [{"id", "namespace", "content",
  "similarity"}]}.
- A failed tool comes back with isError true and text "<tool> failed: <reason>".
- Headers: x-memory-invocation: hook on calls the plugin makes itself; no invocation header on
  calls the model makes; x-session-id: <Hermes session id> on both.
- mcp SDK objects: convert with model_dump(by_alias=True, mode="json", exclude_none=True) and read
  camelCase keys ("inputSchema", "structuredContent", "isError"). Do not guess attribute names.

Prose rules, on code comments, docstrings, test names and your reply: no em dashes (U+2014).
Active voice with a human subject. No adverbs where a plain verb works. No "Note that", no "Here's
what", no "not X, it's Y". Comments say why and flag traps in two or three lines; they never
narrate the code. Test names are snake_case sentences that state the property. No AI attribution.

Return JSON: {"files_written": [], "tests": "<pytest summary line>", "not_done": [],
"open_risks": [], "wire_in": []}.
```

## Shape of the work

```
L0 spike (lead) -> L1 lock (lead, one commit)
   -> T1 bridge (opus)  --\
   -> T2 auth   (opus)  ---+--> W wiring pass (lead) -> R1 review (opus) -> fixes -> gate
   -> T3 pure modules (sonnet) -> T4 provider (sonnet) --/
   -> T5 wizard, importer, cli (sonnet) ------------------/
   -> T6 package README (content-writer, sonnet), after T4 and T5
```

T1, T2, T3 and T5 start together once L1 lands. T4 starts when T3 returns, because its tests use the
real `recall` and `gate` modules and a fake bridge. Writing fans out; verification is sequential and
belongs to the lead.

| id | purpose | owns | model | agent | depends on | output contract | gate |
|---|---|---|---|---|---|---|---|
| L0 | prove the SDK meets the engine; capture fixtures | `scripts/lib/hermes_plugin_capture.py`, `client/hermes/tools_snapshot.json`, `client/hermes/tests/fixtures/engine_transcript.json` | lead (opus) | lead | none | two JSON files as specified below; the handshake method recorded | capture prints `handshake=<method>` and the tool names |
| L1 | interface lock | every file in the L1 list | lead (opus) | lead | L0 | stubs with exact signatures; `config.py`, `conftest.py`, `fakes.py` complete | `pytest tests/test_config.py tests/test_fakes.py` passes; wheel lists `lumberroom_hermes/__init__.py` |
| T1 | MCP bridge | `bridge.py`, `tests/test_bridge.py` | opus | implementer | L1 | `Bridge` per the lock | `pytest tests/test_bridge.py` passes |
| T2 | auth and token store | `auth.py`, `tokens.py`, `tests/test_auth.py`, `tests/test_tokens.py` | opus | implementer | L1 | `build_auth`, `login`, `logout`, `FileTokenStorage`, `RefreshFence` per the lock | `pytest tests/test_auth.py tests/test_tokens.py` passes |
| T3 | recall, gate, schemas | `recall.py`, `gate.py`, `schemas.py`, `tests/test_recall.py`, `tests/test_gate.py`, `tests/test_schemas.py` | sonnet | implementer | L1 | pure functions per the lock; `gate.py` applies spec §8's session and turn rules | their three test files pass |
| T4 | provider hooks | `provider.py`, `tests/test_provider.py` | sonnet | implementer | L1, T3 | `LumberroomProvider` per spec §7 to §10, gating every prefetch and tool call on the current turn's author | `pytest tests/test_provider.py` passes |
| T5 | setup, import, CLI | `wizard.py`, `importer.py`, `cli.py`, `tests/test_wizard.py`, `tests/test_importer.py`, `tests/test_cli.py` | sonnet | implementer | L1 | per spec §11, §12 | their three test files pass |
| T6 | package README | `client/hermes/README.md` | sonnet | content-writer | T4, T5 | install, setup, config table, failure table, what it does not do | em dash grep clean; lead read against spec |
| W | wiring pass | `__init__.py`, `plugin.yaml`, `pyproject.toml`, `tests/conftest.py`, `tests/fakes.py`, `tests/test_contract.py`, `scripts/hermes-plugin-test.sh`, `scripts/lib/hermes_plugin_drive.py`, `CHANGELOG.md`, `README.md`, `CONTRIBUTING.md`, `.gitignore` | lead (opus) | lead | T1 to T5 | green unit suite, green gate script | full pytest run and `scripts/hermes-plugin-test.sh` print no FAIL |
| R1 | whole-branch review | read-only | opus | reviewer | W | findings with file:line, severity, fix | lead checks each claim against the code before acting |

If a delegated agent spends a large budget and returns no usable output, the lead does that task
directly and does not re-delegate it.

---

## L0. Protocol spike (lead)

The one unknown the rest rests on: whether the `mcp` 2.0.0 client completes a handshake with the
engine's rmcp 3.1 stateless server, and which call it takes. Hermes carries a fallback for servers
that answer `initialize` with a version the SDK refuses (`tools/mcp_tool_transport.py:143-208`), and
it tries `server/discover` only on a protocol-era signal (`tools/mcp_tool_errors.py:29-34`).

- [ ] **Step 1: build the plugin venv.**

```bash
cd /Users/aditya/work/cbrspn-tech/lumberroom/.claude/worktrees/hermes-plugin
/opt/homebrew/bin/python3.14 -m venv .venv-hermes-plugin
.venv-hermes-plugin/bin/pip install "/Users/aditya/work/open-source/hermes-agent[mcp]"
.venv-hermes-plugin/bin/pip install "mcp>=2.0.0,<3" "httpx2>=2.7.0,<3" "filelock>=3.12,<4" \
  "pytest==9.1.1" "pytest-asyncio==1.3.0"
.venv-hermes-plugin/bin/python -c "import agent.memory_provider, plugins.memory, mcp, httpx2, filelock; print('ok')"
```

Expected: `ok`. If `plugins.memory` does not import from the installed wheel, install Hermes
editable (`pip install -e`) and record that in the gate script's `ensure_venv`.

- [ ] **Step 2: write `scripts/lib/hermes_plugin_capture.py`.**

```python
"""Capture the engine's MCP handshake, tools/list and one tools/call for the Hermes plugin.

Writes client/hermes/tools_snapshot.json (the schemas the plugin routes before it connects) and
client/hermes/tests/fixtures/engine_transcript.json (what the unit-test fake replays). Run by
scripts/hermes-plugin-test.sh --capture against a scratch engine in token mode.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

KEEP_HEADERS = ("content-type", "mcp-protocol-version", "www-authenticate")
# What Hermes reads as "this server speaks the stateless era, try server/discover"
# (hermes-agent tools/mcp_tool_errors.py:29-34). Any other failure is its own diagnosis.
MODERN_SIGNALS = ("-32022", "-32601", "method not found", "unsupported protocol version")


def _json(raw: bytes):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw.decode("utf-8", "replace")


def _leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


def _rejected_as_modern(exc: BaseException) -> bool:
    # A timeout or a dropped socket carries none of these signals. Falling back on one would
    # record a handshake mismatch this spike never saw, which is the question it exists to answer.
    exc = _leaf(exc)
    code = getattr(getattr(exc, "error", None), "code", None)
    text = f"{code} {exc}".lower()
    return any(signal in text for signal in MODERN_SIGNALS)


async def capture(url: str, token: str):
    """((handshake, server info, tools, exchanges), None), or (None, a one-line diagnosis)."""
    exchanges = []

    async def record(response):
        await response.aread()
        request = response.request
        exchanges.append({
            "request": {"method": request.method, "path": request.url.path,
                        "headers": {k: v for k, v in request.headers.items() if k.lower() in KEEP_HEADERS},
                        "json": _json(request.content)},
            "response": {"status": response.status_code,
                         "headers": {k: v for k, v in response.headers.items() if k.lower() in KEEP_HEADERS},
                         "json": _json(response.content)},
        })

    headers = {"authorization": f"Bearer {token}", "x-memory-invocation": "hook"}
    async with httpx2.AsyncClient(headers=headers, event_hooks={"response": [record]}, timeout=10.0) as http:
        async with streamable_http_client(f"{url}/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                # The engine speaks 2026-07-28 statelessly. Hermes found servers that answer
                # initialize with a version the SDK refuses; discover is the other door, and only
                # a protocol-era signal opens it.
                try:
                    result = await session.initialize()
                    handshake = "initialize"
                except Exception as first:
                    if not _rejected_as_modern(first):
                        return None, f"initialize failed with no protocol-era signal: {_leaf(first)!r}"
                    print(f"initialize rejected: {_leaf(first)!r}; trying server/discover", file=sys.stderr)
                    try:
                        result = await session.discover()
                        handshake = "discover"
                    except Exception as second:
                        return None, (f"both handshakes failed. initialize: {_leaf(first)!r}. "
                                      f"discover: {_leaf(second)!r}")
                listing = await session.list_tools()
                await session.call_tool("memory_search", {"query": "capture probe", "limit": 1})
    info = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    tools = [t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in listing.tools]
    return (handshake, info, tools, exchanges), None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--transcript", type=Path, required=True)
    a = ap.parse_args()
    captured, failure = asyncio.run(capture(a.url, a.token))
    if failure:
        print(failure, file=sys.stderr)
        return 2
    handshake, info, tools, exchanges = captured
    snapshot = {"handshake": handshake, "server_info": info.get("serverInfo"),
                "instructions": info.get("instructions"), "tools": tools}
    a.snapshot.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    a.transcript.parent.mkdir(parents=True, exist_ok=True)
    a.transcript.write_text(json.dumps({"handshake": handshake, "exchanges": exchanges}, indent=2) + "\n")
    print(f"handshake={handshake} tools={','.join(t['name'] for t in tools)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: run it against a scratch engine in token mode.**

```bash
export POSTGRES_PASSWORD=...   # the same value the other gates use
TOKEN="$(openssl rand -hex 32)"
SCRATCH_DB=lumberroom_hermes_plugin_test SCRATCH_NAME=lumberroom-hermes-plugin-test-server \
SCRATCH_PORT=8796 SCRATCH_KEEP=0 \
SCRATCH_TOKENS="[{\"client\":\"hermes-plugin-test\",\"token\":\"$TOKEN\",\"mayDelete\":true,\"mayIngest\":true}]" \
bash -c '. scripts/lib/scratch-server.sh && scratch_start && \
  .venv-hermes-plugin/bin/python scripts/lib/hermes_plugin_capture.py --url "$SCRATCH_URL" --token "'"$TOKEN"'" \
    --snapshot client/hermes/tools_snapshot.json \
    --transcript client/hermes/tests/fixtures/engine_transcript.json; rc=$?; scratch_stop; exit $rc'
```

Expected: `handshake=initialize tools=context_bootstrap,memory_search,memory_write,registry_get,memory_forget,alias_list,review_queue,review_decide`
(the grant carries `mayDelete`, so `memory_forget` appears; `ENG/src/mcp/capability.rs:51-80`).

If the script exits 2, read the one line it printed to stderr:

- `initialize failed with no protocol-era signal`: a timeout, a refused connection or a 401. The
  handshake question is still open. Fix the scratch engine or the token and run it again.
- `both handshakes failed`: the SDK and the engine share no handshake. Stop the plan. That is the
  reversal condition of decision 0021's first cost, and the owner decides between patching the
  client and the fallback in the decision record. When the initialize half reads `Unsupported
  protocol version from the server`, the engine answered and the SDK refused its version, the case
  Hermes completes by hand (`tools/mcp_tool_transport.py:162-172,188-208`). Put that line in front
  of the owner with the decision.

- [ ] **Step 4: record the outcome** in the lock commit message: the handshake method, the
  `mcp-protocol-version` the engine answered, and the tool names.

---

## L1. The interface lock (lead, one commit)

Everything below lands in one commit before any fan-out. Stubs raise `NotImplementedError("<task>")`.

- [ ] **`.gitignore`**: append `.venv-hermes-plugin/`.

- [ ] **`client/hermes/pyproject.toml`**

```toml
[build-system]
requires = ["hatchling>=1.26,<2"]
build-backend = "hatchling.build"

[project]
name = "lumberroom-hermes"
version = "0.1.0"
description = "lumberroom as Hermes Agent's memory provider: recall on every turn, writes through the engine's own tools"
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
authors = [{ name = "the-cybersapien" }]
# Upper bounds on purpose: Hermes installs a directory provider's dependencies under its own exact
# pins and refuses a plugin whose ranges cannot resolve against them.
dependencies = [
  "mcp>=2.0.0,<3",
  "httpx2>=2.7.0,<3",
  "filelock>=3.12,<4",
]

[project.optional-dependencies]
test = ["pytest==9.1.1", "pytest-asyncio==1.3.0"]

[project.entry-points."hermes_agent.memory_providers"]
lumberroom = "lumberroom_hermes:register"

[project.urls]
Source = "https://github.com/the-cybersapien/lumberroom/tree/main/client/hermes"

# This directory is the package. Hermes loads it in place from ~/.hermes/plugins/lumberroom, and
# the wheel maps the same files under lumberroom_hermes/.
[tool.hatch.build.targets.wheel]
only-include = [
  "__init__.py", "auth.py", "bridge.py", "cli.py", "config.py", "gate.py", "importer.py",
  "plugin.yaml", "provider.py", "recall.py", "schemas.py", "tokens.py", "tools_snapshot.json",
  "wizard.py",
]
sources = { "" = "lumberroom_hermes" }

[tool.hatch.build.targets.sdist]
exclude = ["tests", ".venv*", "__pycache__"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--import-mode=importlib"
asyncio_mode = "auto"
```

- [ ] **`client/hermes/plugin.yaml`**

```yaml
name: lumberroom
version: 0.1.0
description: "lumberroom: durable memory shared with every agent you use. Recall on every turn, writes through the engine's own tools. Self-hosted or lumberroom.cloud."
```

- [ ] **`client/hermes/__init__.py`**

```python
"""lumberroom as Hermes Agent's memory provider.

Hermes picks this directory out by the text register_memory_provider in this file, before it
imports anything, so the call below has to stay spelled out here.
"""

__version__ = "0.1.0"


def register(ctx) -> None:
    # Imported here so `import lumberroom_hermes` stays cheap for the entry-point scan.
    from .provider import LumberroomProvider

    ctx.register_memory_provider(LumberroomProvider())
```

- [ ] **`client/hermes/config.py`** (complete in the lock; T-tasks read it, nobody edits it)

```python
"""The memory.lumberroom block of a Hermes profile's config.yaml, parsed once and validated."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

HOSTED_BASE_URL = "https://mcp.lumberroom.cloud"
TOKEN_ENV = "LUMBERROOM_HERMES_TOKEN"
DEFAULT_TOOLS: tuple[str, ...] = ("memory_search", "memory_write", "registry_get", "memory_forget")
DEFAULT_LOCAL_PLATFORMS: tuple[str, ...] = ("cli", "tui", "desktop", "acp", "cron")
AuthMode = Literal["token", "oauth"]

_INT_RANGES = {
    "digest_max_chars": (0, 50_000),
    "recall_limit": (1, 20),
    "recall_max_chars": (0, 20_000),
    "review_interval": (0, 1_000),
    "oauth_callback_port": (1024, 65535),
}
# prefetch stays under Hermes's 8.0s join, or the host skips the provider on later turns.
_FLOAT_RANGES = {
    "prefetch_timeout_s": (0.5, 7.5),
    "tool_timeout_s": (1.0, 120.0),
    "connect_timeout_s": (0.5, 30.0),
}
_BOOLS = ("recall", "digest")
_LISTS = ("tools", "owner_user_ids", "local_platforms")
_KNOWN = {"base_url", "auth", "project", *_INT_RANGES, *_FLOAT_RANGES, *_BOOLS, *_LISTS}


class ConfigError(ValueError):
    """Names the offending key: the host shows this text and nothing else."""


@dataclass(frozen=True)
class LumberroomConfig:
    base_url: str
    auth: AuthMode
    project: str = "auto"
    recall: bool = True
    digest: bool = True
    digest_max_chars: int = 6000
    recall_limit: int = 4
    recall_max_chars: int = 1200
    review_interval: int = 10
    tools: tuple[str, ...] = DEFAULT_TOOLS
    owner_user_ids: tuple[str, ...] = ()
    local_platforms: tuple[str, ...] = DEFAULT_LOCAL_PLATFORMS
    prefetch_timeout_s: float = 3.0
    tool_timeout_s: float = 20.0
    connect_timeout_s: float = 3.0
    oauth_callback_port: int = 47631

    @property
    def origin(self) -> str:
        return self.base_url[: -len("/mcp")] if self.base_url.endswith("/mcp") else self.base_url

    @property
    def mcp_url(self) -> str:
        return self.origin + "/mcp"


def section(config: Mapping[str, Any]) -> dict[str, Any]:
    memory = config.get("memory") if isinstance(config, Mapping) else None
    block = memory.get("lumberroom") if isinstance(memory, Mapping) else None
    return dict(block) if isinstance(block, Mapping) else {}


def _base_url(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("memory.lumberroom.base_url is required: the engine's URL, such as http://127.0.0.1:8787")
    url = raw.strip().rstrip("/")
    scheme, sep, rest = url.partition("://")
    if not sep or scheme.lower() not in ("http", "https") or not rest:
        raise ConfigError(f"memory.lumberroom.base_url must start with http:// or https://, got {raw!r}")
    return scheme.lower() + "://" + rest


def parse(block: Mapping[str, Any]) -> LumberroomConfig:
    unknown = sorted(set(block) - _KNOWN)
    if unknown:
        raise ConfigError(f"unknown key memory.lumberroom.{unknown[0]}")
    auth = block.get("auth")
    if auth not in ("token", "oauth"):
        raise ConfigError("memory.lumberroom.auth must be token or oauth")
    values: dict[str, Any] = {"base_url": _base_url(block.get("base_url")), "auth": auth}
    if "project" in block:
        project = block["project"]
        if not isinstance(project, str) or not project.strip():
            raise ConfigError("memory.lumberroom.project must be auto, none, or a slug or path")
        values["project"] = project.strip()
    for key in _BOOLS:
        if key in block:
            if not isinstance(block[key], bool):
                raise ConfigError(f"memory.lumberroom.{key} must be true or false")
            values[key] = block[key]
    for key, (lo, hi) in _INT_RANGES.items():
        if key in block:
            v = block[key]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ConfigError(f"memory.lumberroom.{key} must be a whole number from {lo} to {hi}")
            values[key] = v
    for key, (lo, hi) in _FLOAT_RANGES.items():
        if key in block:
            v = block[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
                raise ConfigError(f"memory.lumberroom.{key} must be a number from {lo} to {hi}")
            values[key] = float(v)
    for key in _LISTS:
        if key in block:
            v = block[key]
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                raise ConfigError(f"memory.lumberroom.{key} must be a list of strings")
            values[key] = tuple(dict.fromkeys(x.strip() for x in v))
    if "tools" in values and not values["tools"]:
        raise ConfigError("memory.lumberroom.tools must name at least one tool")
    for owner in values.get("owner_user_ids", ()):
        platform, sep, uid = owner.partition(":")
        if not sep or not platform or not uid:
            raise ConfigError(f"memory.lumberroom.owner_user_ids entries look like telegram:123456789, got {owner!r}")
    return LumberroomConfig(**values)


def load() -> LumberroomConfig:
    """Parse the active profile's block. Hermes resolves the profile from its context."""
    from hermes_cli.config import load_config

    return parse(section(load_config()))


def builtin_flags(config: Mapping[str, Any]) -> tuple[bool, bool]:
    """(memory_enabled, user_profile_enabled), both defaulting to on as Hermes does."""
    memory = config.get("memory") if isinstance(config, Mapping) else None
    memory = memory if isinstance(memory, Mapping) else {}
    return (memory.get("memory_enabled", True) is not False, memory.get("user_profile_enabled", True) is not False)


def write_section(config: dict[str, Any], values: Mapping[str, Any]) -> None:
    memory = config.setdefault("memory", {})
    block = memory.setdefault("lumberroom", {})
    block.update(values)
```

- [ ] **`client/hermes/schemas.py`** (stub, T3 implements)

```python
"""Tool schemas: the bundled snapshot, the per-profile cache, and MCP to Hermes conversion."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

SNAPSHOT_FILE = Path(__file__).with_name("tools_snapshot.json")
CACHE_DIR = "lumberroom"
CACHE_NAME = "tools_cache.json"


def load_snapshot() -> dict[str, Any]:
    """{"handshake", "server_info", "instructions", "tools": [MCP tool dicts]} as L0 captured it."""
    raise NotImplementedError("T3")


def read_cache(hermes_home: str) -> dict[str, Any] | None:
    """The last live listing for this profile, same shape as the snapshot; None when absent or unreadable."""
    raise NotImplementedError("T3")


def write_cache(hermes_home: str, listing: Mapping[str, Any]) -> None:
    """Atomic write of {"instructions", "tools"} to $HERMES_HOME/lumberroom/tools_cache.json."""
    raise NotImplementedError("T3")


def to_hermes_schema(tool: Mapping[str, Any]) -> dict[str, Any]:
    """MCP {"name", "description", "inputSchema"} to Hermes {"name", "description", "parameters"}."""
    raise NotImplementedError("T3")


def select(allowlist: Sequence[str], tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The MCP tool dicts whose names the allowlist carries, in allowlist order."""
    raise NotImplementedError("T3")
```

- [ ] **`client/hermes/recall.py`** (stub, T3 implements)

```python
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
```

- [ ] **`client/hermes/gate.py`** (stub, T3 implements)

```python
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
```

- [ ] **`client/hermes/tokens.py`** (stub, T2 implements)

```python
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
```

- [ ] **`client/hermes/auth.py`** (stub, T2 implements)

```python
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
```

- [ ] **`client/hermes/bridge.py`** (stub, T1 implements)

```python
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
```

- [ ] **`client/hermes/provider.py`** (stub, T4 implements)

```python
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
```

- [ ] **`client/hermes/wizard.py`**, **`importer.py`**, **`cli.py`** (stubs, T5 implements)

```python
# wizard.py
"""hermes memory setup lumberroom: pick the deployment, the credential, and turn the built-in store off."""

from __future__ import annotations

from typing import Any, Callable, Mapping


def get_config_schema() -> list[dict[str, Any]]:
    raise NotImplementedError("T5")


def apply_builtin_off(config: dict[str, Any]) -> list[str]:
    """Set memory.provider, memory_enabled, user_profile_enabled, nudge_interval. Returns the lines changed."""
    raise NotImplementedError("T5")


def save_values(values: Mapping[str, Any], hermes_home: str) -> None:
    raise NotImplementedError("T5")


def post_setup(hermes_home: str, config: dict[str, Any], *,
               ask: Callable[[str], str] = input,
               ask_secret: Callable[[str], str] | None = None,
               out: Callable[[str], None] = print) -> None:
    raise NotImplementedError("T5")
```

```python
# importer.py
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
```

```python
# cli.py
"""hermes lumberroom login | logout | status | import-builtin.

Hermes imports this file during argparse setup, before any provider loads, so heavy imports wait
inside the handlers.
"""

from __future__ import annotations

from typing import Any


def register_cli(subparser: Any) -> None:
    raise NotImplementedError("T5")


def lumberroom_command(args: Any) -> int:
    raise NotImplementedError("T5")
```

- [ ] **`client/hermes/tests/conftest.py`** (lead, complete)

```python
"""Loads client/hermes as the package lumberroom_hermes, the way a wheel install names it."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_package() -> None:
    if "lumberroom_hermes" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "lumberroom_hermes", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["lumberroom_hermes"] = module
    spec.loader.exec_module(module)


_load_package()


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    (home / "memories").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture
def cfg():
    from lumberroom_hermes.config import LumberroomConfig

    return LumberroomConfig(base_url="http://fake.lumberroom.test", auth="token")


@pytest.fixture
def fake_engine():
    from fakes import FakeEngine

    return FakeEngine()
```

- [ ] **`client/hermes/tests/fakes.py`** (lead, complete). The fake answers from the L0 transcript.

```python
"""A stand-in for the engine's /mcp and /admin/ingest routes, replaying the L0 transcript.

Loop-agnostic on purpose: the bridge sends from its own loop thread, and a server that owns a task
group would bind to whichever loop entered it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx2

TRANSCRIPT = Path(__file__).parent / "fixtures" / "engine_transcript.json"
BASE_URL = "http://fake.lumberroom.test"


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


def _captured(method: str) -> dict[str, Any]:
    for x in json.loads(TRANSCRIPT.read_text())["exchanges"]:
        req = x["request"]["json"]
        if isinstance(req, dict) and req.get("method") == method:
            return x["response"]["json"]["result"]
    raise KeyError(method)


@dataclass
class FakeEngine:
    requests: list[Recorded] = field(default_factory=list)
    delay_s: float = 0.0
    status_override: int | None = None
    hide_tools: set[str] = field(default_factory=set)
    answers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = field(default_factory=dict)
    admin: dict[tuple[str, str], Callable[[Any], tuple[int, Any]]] = field(default_factory=dict)

    def answer(self, tool: str, *, structured: dict[str, Any] | None = None, text: str = "",
               error: str | None = None) -> None:
        def fn(_args: dict[str, Any]) -> dict[str, Any]:
            if error is not None:
                return {"content": [{"type": "text", "text": error}], "isError": True}
            return {"content": [{"type": "text", "text": text or json.dumps(structured or {})}],
                    "structuredContent": structured or {}, "isError": False}
        self.answers[tool] = fn

    def tool_calls(self) -> list[Recorded]:
        return [r for r in self.requests if isinstance(r.body, dict) and r.body.get("method") == "tools/call"]

    def client_factory(self, **kwargs: Any) -> httpx2.AsyncClient:
        kwargs.pop("transport", None)
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app), **kwargs)

    async def app(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        raw = b""
        while True:
            message = await receive()
            raw += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        body = json.loads(raw) if raw else None
        self.requests.append(Recorded(scope["method"], scope["path"], headers, body))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.status_override:
            await self._send(send, self.status_override, {"error": "unauthorized"},
                             [(b"www-authenticate", b'Bearer resource_metadata="http://fake.lumberroom.test/.well-known/oauth-protected-resource"')])
            return
        if scope["path"] == "/mcp":
            await self._mcp(send, body)
            return
        handler = self.admin.get((scope["method"], scope["path"]))
        status, payload = handler(body) if handler else (404, {"error": "not found"})
        await self._send(send, status, payload)

    async def _mcp(self, send, body: Any) -> None:
        method = body.get("method")
        if "id" not in body:                     # a notification
            await self._send(send, 202, None)
            return
        if method in ("initialize", "server/discover"):
            result = _captured(method)
        elif method == "tools/list":
            result = dict(_captured("tools/list"))
            result["tools"] = [t for t in result["tools"] if t["name"] not in self.hide_tools]
        elif method == "tools/call":
            name = body["params"]["name"]
            fn = self.answers.get(name)
            result = fn(body["params"].get("arguments") or {}) if fn else {
                "content": [{"type": "text", "text": "{}"}], "structuredContent": {}, "isError": False}
        else:
            await self._send(send, 200, {"jsonrpc": "2.0", "id": body["id"],
                                         "error": {"code": -32601, "message": f"unknown method {method}"}})
            return
        await self._send(send, 200, {"jsonrpc": "2.0", "id": body["id"], "result": result})

    @staticmethod
    async def _send(send, status: int, payload: Any, extra: list | None = None) -> None:
        data = b"" if payload is None else json.dumps(payload).encode()
        headers = [(b"content-type", b"application/json")] + (extra or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": data})
```

- [ ] **`client/hermes/tests/test_fakes.py`** (lead). Proves the fake speaks enough protocol for
  the real SDK client, which every later test leans on.

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from fakes import BASE_URL


async def test_the_sdk_client_lists_the_captured_tools_through_the_fake(fake_engine):
    async with fake_engine.client_factory(base_url=BASE_URL) as http:
        async with streamable_http_client(f"{BASE_URL}/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                listing = await session.list_tools()
    names = {t.name for t in listing.tools}
    assert {"memory_search", "memory_write", "registry_get"} <= names


async def test_the_fake_records_the_headers_a_client_sent(fake_engine):
    async with fake_engine.client_factory(base_url=BASE_URL, headers={"x-session-id": "s-1"}) as http:
        await http.post(f"{BASE_URL}/admin/ingest/runs", json={"extractor": "x"})
    assert fake_engine.requests[-1].headers["x-session-id"] == "s-1"
```

If L0 recorded `handshake=discover`, the first test calls `session.discover()`.

- [ ] **`client/hermes/tests/test_config.py`** (lead)

```python
import pytest

from lumberroom_hermes.config import ConfigError, parse


@pytest.mark.parametrize("raw", ["https://Host.example", "https://Host.example/", "https://Host.example/mcp",
                                 "https://Host.example/mcp/", "HTTPS://Host.example"])
def test_five_spellings_of_one_engine_give_one_mcp_url(raw):
    assert parse({"base_url": raw, "auth": "token"}).mcp_url == "https://Host.example/mcp"


@pytest.mark.parametrize("block, key", [
    ({"auth": "token"}, "base_url"),
    ({"base_url": "host:8787", "auth": "token"}, "base_url"),
    ({"base_url": "http://h"}, "auth"),
    ({"base_url": "http://h", "auth": "token", "recall_limit": 50}, "recall_limit"),
    ({"base_url": "http://h", "auth": "token", "prefetch_timeout_s": 8.0}, "prefetch_timeout_s"),
    ({"base_url": "http://h", "auth": "token", "owner_user_ids": ["123"]}, "owner_user_ids"),
    ({"base_url": "http://h", "auth": "token", "recal": True}, "recal"),
])
def test_a_bad_block_names_the_key(block, key):
    with pytest.raises(ConfigError, match=key):
        parse(block)


def test_defaults_match_the_spec():
    c = parse({"base_url": "http://h", "auth": "oauth"})
    assert (c.digest_max_chars, c.recall_limit, c.recall_max_chars, c.review_interval) == (6000, 4, 1200, 10)
    assert c.local_platforms == ("cli", "tui", "desktop", "acp", "cron")
    assert c.owner_user_ids == ()
```

- [ ] **L1 gate.**

```bash
cd client/hermes && ../../.venv-hermes-plugin/bin/python -m pytest tests/test_config.py tests/test_fakes.py -q
cd ../.. && .venv-hermes-plugin/bin/pip wheel --no-deps -w /tmp/lr-hermes-wheel client/hermes
.venv-hermes-plugin/bin/python -m zipfile -l /tmp/lr-hermes-wheel/lumberroom_hermes-0.1.0-py3-none-any.whl
grep -rP '\x{2014}' client/hermes scripts/lib/hermes_plugin_capture.py
```

Expected: pytest ends `passed` with 0 failed; the zip listing shows `lumberroom_hermes/__init__.py`,
`lumberroom_hermes/cli.py`, `lumberroom_hermes/plugin.yaml` and `lumberroom_hermes/tools_snapshot.json`
and no `tests/`; the grep prints nothing. If hatch's `sources` mapping does not produce
`lumberroom_hermes/`, switch the build backend to setuptools with
`package-dir = {"lumberroom_hermes" = "."}` in this same commit, before fan-out.

- [ ] **Commit L1.** The lead stages explicit paths and commits as the owner.

---

## T1. The MCP bridge (opus, implementer)

**Files:** `client/hermes/bridge.py`, `client/hermes/tests/test_bridge.py`.

**Interfaces.** Consumes `LumberroomConfig`, `AuthHandle` (`headers()`, `httpx_auth()`,
`refresh_guard()`), `LoginRequired` from `auth`. Produces the `Bridge`, `CallResult`, `ToolsListing`
contract in the lock, unchanged.

**Behaviour** (spec §5):

- `start()` creates an asyncio loop on a daemon thread from `spawn_context_thread(target, name="lumberroom-bridge")`.
  Sessions open lazily on first use, one per invocation kind, each inside its own
  `contextlib.AsyncExitStack`: `client_factory(headers=..., auth=auth.httpx_auth(),
  timeout=httpx2.Timeout(tool_timeout_s, connect=connect_timeout_s), follow_redirects=True,
  base_url=cfg.origin)` (default factory `httpx2.AsyncClient`), then
  `streamable_http_client(cfg.mcp_url, http_client=client)`, then
  `ClientSession(r, w, client_info=Implementation(name="lumberroom-hermes", version=__version__))` and the
  handshake L0 recorded. Hook headers: `x-memory-invocation: hook`, `x-session-id`, `user-agent:
  lumberroom-hermes/<version>`, plus `auth.headers()`. Model headers: the same without
  `x-memory-invocation`.
- `list_tools` reads the handshake result's `instructions` and `tools/list` on the hook session.
- Each call runs `async with auth.refresh_guard():` around the session call, and
  `asyncio.wait_for` with the caller's timeout. The synchronous side waits on
  `run_coroutine_threadsafe(...).result(timeout + 0.1)` and cancels the future on expiry.
- Mapping to `kind`: success; `isError` true gives `tool_error` with the text; `httpx2.ConnectError`
  or `ConnectTimeout` gives `unreachable`; a timeout after send gives `timeout`; an HTTP 401 gives
  `unauthorized`; `LoginRequired` anywhere gives `login_required`.
- After `unreachable`, `timeout` or any exception inside a session, close that session's exit stack
  so the next call opens a fresh one. Never retry a call.
- `set_session_id` updates `client.headers["x-session-id"]` on both open clients and on the stored
  header template for sessions opened later.
- `http_json` uses a third client with the same headers minus `x-memory-invocation` and the same
  auth, against `cfg.origin + path`.
- `close` cancels in-flight calls, closes every exit stack and stops the loop within `timeout`.

- [ ] **Step 1: write the failing tests.**

```python
import contextlib
import time

import httpx2
import pytest

from lumberroom_hermes.auth import LoginRequired
from lumberroom_hermes.bridge import Bridge


class StaticAuth:
    mode = "token"

    def __init__(self, raise_login=False):
        self.raise_login = raise_login

    def headers(self):
        return {"authorization": "Bearer t-1"}

    def httpx_auth(self):
        return None

    @contextlib.asynccontextmanager
    async def refresh_guard(self):
        if self.raise_login:
            raise LoginRequired("no browser here")
        yield


@pytest.fixture
def bridge(fake_engine, cfg):
    b = Bridge(cfg, StaticAuth(), session_id="s-1", client_factory=fake_engine.client_factory)
    b.start()
    yield b
    b.close()


def test_hook_calls_carry_the_hook_header_and_model_calls_carry_none(bridge, fake_engine):
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    hook, model = fake_engine.tool_calls()
    assert hook.headers["x-memory-invocation"] == "hook"
    assert "x-memory-invocation" not in model.headers
    assert hook.headers["x-session-id"] == model.headers["x-session-id"] == "s-1"
    assert hook.headers["authorization"] == "Bearer t-1"


def test_a_session_switch_changes_the_header_on_both_sessions(bridge, fake_engine):
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    bridge.set_session_id("s-2")
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    assert [c.headers["x-session-id"] for c in fake_engine.tool_calls()[2:]] == ["s-2", "s-2"]


def test_structured_content_comes_back_unchanged(bridge, fake_engine):
    payload = {"id": "a", "namespace": "user:me", "sensitivity": "open", "deduplicated": False,
               "possible_conflicts": [{"id": "b", "namespace": "user:me", "content": "old", "similarity": 0.93}]}
    fake_engine.answer("memory_write", structured=payload)
    r = bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    assert r.kind == "ok" and r.structured == payload


def test_an_engine_refusal_is_a_tool_error_carrying_its_text(bridge, fake_engine):
    fake_engine.answer("memory_write", error="memory_write failed: namespace outside the grant")
    r = bridge.call("memory_write", {"content": "c", "namespace": "x:y"}, invocation="model", timeout=5)
    assert r.kind == "tool_error" and "outside the grant" in r.error


def test_a_call_that_outlives_its_timeout_returns_within_the_bound(bridge, fake_engine):
    bridge.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
    fake_engine.delay_s = 5
    started = time.monotonic()
    r = bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=0.5)
    assert r.kind == "timeout"
    assert time.monotonic() - started < 0.8


def test_call_many_shares_one_deadline(bridge, fake_engine):
    bridge.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
    fake_engine.delay_s = 0.3
    started = time.monotonic()
    results = bridge.call_many([("context_bootstrap", {}), ("memory_search", {"query": "q"})],
                               invocation="hook", timeout=2)
    assert [r.kind for r in results] == ["ok", "ok"]
    assert time.monotonic() - started < 0.55


def test_an_engine_that_refuses_connections_is_unreachable(cfg):
    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)

    def refusing(**kw):
        kw.pop("transport", None)
        return httpx2.AsyncClient(transport=httpx2.MockTransport(refuse), **kw)
    b = Bridge(cfg, StaticAuth(), session_id="s", client_factory=refusing)
    b.start()
    try:
        assert b.call("memory_search", {"query": "q"}, invocation="hook", timeout=2).kind == "unreachable"
    finally:
        b.close()


def test_a_401_is_unauthorized(bridge, fake_engine):
    fake_engine.status_override = 401
    assert bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "unauthorized"


def test_login_required_from_the_auth_guard_surfaces_as_its_own_kind(fake_engine, cfg):
    b = Bridge(cfg, StaticAuth(raise_login=True), session_id="s", client_factory=fake_engine.client_factory)
    b.start()
    try:
        assert b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "login_required"
    finally:
        b.close()


def test_a_session_that_lost_its_connection_reconnects_on_the_next_call(bridge, fake_engine):
    fake_engine.status_override = 503
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=2)
    fake_engine.status_override = None
    assert bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "ok"


def test_list_tools_returns_the_engine_instructions_and_tools(bridge):
    result, listing = bridge.list_tools(timeout=5)
    assert result.kind == "ok"
    assert "memory_write" in {t["name"] for t in listing.tools}
    assert listing.instructions and "memory_write" in listing.instructions


def test_http_json_goes_to_the_origin_without_the_invocation_header(bridge, fake_engine):
    fake_engine.admin[("POST", "/admin/ingest/runs")] = lambda body: (200, {"run_id": "r-1"})
    status, body = bridge.http_json("POST", "/admin/ingest/runs", {"extractor": "x"}, timeout=5)
    assert (status, body) == (200, {"run_id": "r-1"})
    assert "x-memory-invocation" not in fake_engine.requests[-1].headers


def test_close_returns_within_two_seconds_with_a_call_in_flight(fake_engine, cfg):
    import threading
    b = Bridge(cfg, StaticAuth(), session_id="s", client_factory=fake_engine.client_factory)
    b.start()
    fake_engine.delay_s = 30
    threading.Thread(target=lambda: b.call("memory_search", {"query": "q"}, invocation="hook", timeout=30), daemon=True).start()
    time.sleep(0.2)
    started = time.monotonic()
    b.close(timeout=2.0)
    assert time.monotonic() - started < 2.3
```

- [ ] **Step 2: run them and watch them fail** with `NotImplementedError("T1")`.

`cd client/hermes && ../../.venv-hermes-plugin/bin/python -m pytest tests/test_bridge.py -q`

- [ ] **Step 3: implement `bridge.py`** to the behaviour above. Keep the lock's signatures.
- [ ] **Step 4: run the tests until they pass.** Paste the summary line.

---

## T2. Auth and the token store (opus, implementer)

**Files:** `client/hermes/auth.py`, `client/hermes/tokens.py`, `client/hermes/tests/test_auth.py`,
`client/hermes/tests/test_tokens.py`.

**Interfaces.** Consumes `LumberroomConfig`, `TOKEN_ENV`. Produces the lock's `auth` and `tokens`
names. The bridge relies on `AuthHandle.headers()`, `httpx_auth()`, `refresh_guard()` and on
`LoginRequired`.

**Behaviour** (spec §6):

- `TokenAuth`: reads `get_secret(TOKEN_ENV)` once in `build_auth`, on the calling thread, and raises
  `AuthConfigError` naming `LUMBERROOM_HERMES_TOKEN` when empty. `headers()` returns the bearer;
  `httpx_auth()` returns None; `refresh_guard()` yields at once.
- `OAuthAuth`: builds `mcp.client.auth.OAuthClientProvider(server_url=cfg.mcp_url,
  client_metadata=OAuthClientMetadata(client_name=CLIENT_NAME, redirect_uris=[f"http://127.0.0.1:{cfg.oauth_callback_port}/callback"],
  grant_types=["authorization_code", "refresh_token"], response_types=["code"],
  token_endpoint_auth_method="none"), storage=FileTokenStorage(...), redirect_handler=...,
  callback_handler=...)`. Right after construction, from `storage.read()`: set
  `provider.context.token_expiry_time = stored.expires_at` and, when stored metadata exists,
  `provider.context.oauth_metadata = OAuthMetadata.model_validate(stored.oauth_metadata)`. After
  a flow completes with metadata the storage lacks, call `storage.save_metadata`. Read the exact
  callback return type from the installed SDK's `OAuthClientProvider.__init__` annotation.
- `refresh_guard()` (spec §6.2 RefreshFence): an in-process `asyncio.Lock`; rebuild the provider
  when `storage.mtime_ns()` changed since the last build; when `expires_at` is None or
  `expires_at - now < REFRESH_SKEW_S`, enter `RefreshFence`, re-read, rebuild when the disk pair is
  fresh (a non-None `expires_at` at least `REFRESH_SKEW_S` away), otherwise hold the fence through
  the caller's request. A None expiry counts as due: the engine always sends `expires_in`
  (`ENG/src/authserver/routes.rs:894`), so None means a partial or hand-edited file, and the fence
  then brackets whatever refresh follows. `FenceTimeout` propagates; the bridge maps it to
  `unreachable`.
- Non-interactive handlers raise `LoginRequired`. `login()` builds interactive handlers: the
  redirect handler opens the browser (`webbrowser.open`) unless `open_browser` is false, and always
  prints `Open this URL to sign in:` followed by the URL alone on the next line. The callback handler
  starts a one-request loopback listener on `127.0.0.1:<oauth_callback_port>` (stdlib
  `http.server` on a worker thread) and a reader of `read_pasted()`, returns the first
  `parse_callback` result, and times out after 300 seconds (design target). `login` then drives one
  `tools/list` through a throwaway MCP session so the SDK completes the flow and persists tokens.
- `FileTokenStorage`: JSON `{"mcp_url", "tokens", "expires_at", "client_info", "oauth_metadata"}`,
  written to a temp file in the same directory, `os.chmod(0o600)`, `os.replace`. A different
  `mcp_url`, invalid JSON or a missing file reads as empty and leaves the file alone. `set_tokens`
  stamps `expires_at = clock() + expires_in` when `expires_in` is set, and writes `null` when it
  is absent.
- `RefreshFence`: `filelock.FileLock(lock_path, timeout=timeout_s)` acquired through
  `asyncio.to_thread`; `filelock.Timeout` becomes `FenceTimeout`.

- [ ] **Step 1: write the failing tests.**

```python
# tests/test_tokens.py
import asyncio
import subprocess
import sys
import time

import pytest
from mcp.shared.auth import OAuthToken

from lumberroom_hermes.tokens import FenceTimeout, FileTokenStorage, RefreshFence, token_paths

URL = "http://fake.lumberroom.test/mcp"


def test_set_tokens_stamps_an_absolute_expiry(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL, clock=lambda: 1000.0)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=3600, refresh_token="r")))
    assert s.read().expires_at == 4600.0


def test_set_tokens_without_expires_in_records_no_expiry(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL, clock=lambda: 1000.0)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer", refresh_token="r")))
    assert s.read().expires_at is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_token_file_is_private(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    assert (tmp_path / "oauth.json").stat().st_mode & 0o777 == 0o600


def test_a_file_for_another_server_reads_as_empty(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    other = FileTokenStorage(tmp_path / "oauth.json", "https://elsewhere.example/mcp")
    assert asyncio.run(other.get_tokens()) is None


def test_a_corrupt_file_reads_as_logged_out_and_stays_on_disk(tmp_path):
    (tmp_path / "oauth.json").write_text("{not json")
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    assert asyncio.run(s.get_tokens()) is None
    assert (tmp_path / "oauth.json").read_text() == "{not json"


def test_token_paths_live_under_the_profile(tmp_path):
    token, lock = token_paths(str(tmp_path))
    assert token == tmp_path / "lumberroom" / "oauth.json" and lock == tmp_path / "lumberroom" / "oauth.lock"


# A child process loads the package by path, as the conftest does: spawn cannot import a test
# module that pytest loaded under a generated name.
CHILD = r"""
import asyncio, importlib.util, sys
from pathlib import Path
plugin = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("lumberroom_hermes", plugin / "__init__.py",
                                              submodule_search_locations=[str(plugin)])
module = importlib.util.module_from_spec(spec); sys.modules["lumberroom_hermes"] = module; spec.loader.exec_module(module)
from lumberroom_hermes.tokens import RefreshFence

async def run():
    async with RefreshFence(Path(sys.argv[2]), timeout_s=10):
        with open(sys.argv[3], "a") as f:
            f.write("start\n")
        await asyncio.sleep(float(sys.argv[4]))
        with open(sys.argv[3], "a") as f:
            f.write("end\n")

asyncio.run(run())
"""


def hold(tmp_path, seconds):
    from conftest import PLUGIN_DIR
    return subprocess.Popen([sys.executable, "-c", CHILD, str(PLUGIN_DIR), str(tmp_path / "oauth.lock"),
                             str(tmp_path / "log"), str(seconds)])


def test_two_processes_take_the_fence_one_at_a_time(tmp_path):
    procs = [hold(tmp_path, 0.3), hold(tmp_path, 0.3)]
    for p in procs:
        assert p.wait(20) == 0
    assert (tmp_path / "log").read_text().split() == ["start", "end", "start", "end"]


def test_a_fence_held_past_the_timeout_raises_fence_timeout(tmp_path):
    holder = hold(tmp_path, 3)
    time.sleep(1.0)

    async def wait_briefly():
        async with RefreshFence(tmp_path / "oauth.lock", timeout_s=0.5):
            pass
    with pytest.raises(FenceTimeout):
        asyncio.run(wait_briefly())
    holder.wait(10)
```

```python
# tests/test_auth.py
import asyncio
import dataclasses
import threading
import time

import pytest

from lumberroom_hermes import auth as auth_mod
from lumberroom_hermes.auth import AuthConfigError, LoginFailed, LoginRequired, build_auth, parse_callback, token_present
from lumberroom_hermes.config import LumberroomConfig
from lumberroom_hermes.tokens import FileTokenStorage, token_paths

TOKEN = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="token")
OAUTH = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="oauth")


def test_token_auth_reads_the_secret_once_on_the_calling_thread(monkeypatch, hermes_home):
    seen = []
    monkeypatch.setattr("agent.secret_scope.get_secret",
                        lambda name, default=None: seen.append((name, threading.get_ident())) or "t-1")
    handle = build_auth(TOKEN, hermes_home=str(hermes_home))
    assert handle.headers() == {"authorization": "Bearer t-1"}
    assert seen == [("LUMBERROOM_HERMES_TOKEN", threading.get_ident())]


def test_token_mode_without_the_secret_names_the_variable(monkeypatch, hermes_home):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: default)
    with pytest.raises(AuthConfigError, match="LUMBERROOM_HERMES_TOKEN"):
        build_auth(TOKEN, hermes_home=str(hermes_home))
    assert token_present(TOKEN) is False


def test_oauth_is_present_without_a_token_file(hermes_home):
    assert token_present(OAUTH) is True


def test_the_two_context_fields_the_plugin_sets_exist_in_the_installed_sdk():
    from mcp.client.auth.oauth2 import OAuthContext
    names = {f.name for f in dataclasses.fields(OAuthContext)}
    assert {"token_expiry_time", "oauth_metadata"} <= names


def test_oauth_seeds_expiry_and_metadata_from_disk(hermes_home):
    from mcp.shared.auth import OAuthToken
    path, _ = token_paths(str(hermes_home))
    path.parent.mkdir(parents=True)
    s = FileTokenStorage(path, OAUTH.mcp_url, clock=lambda: 1000.0)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=30, refresh_token="r")))
    s.save_metadata({"issuer": "http://fake.lumberroom.test", "authorization_endpoint": "http://fake.lumberroom.test/oauth/authorize",
                     "token_endpoint": "http://fake.lumberroom.test/oauth/token", "response_types_supported": ["code"]})
    handle = build_auth(OAUTH, hermes_home=str(hermes_home))
    ctx = handle.httpx_auth().context
    assert ctx.token_expiry_time == 1030.0
    assert str(ctx.oauth_metadata.token_endpoint) == "http://fake.lumberroom.test/oauth/token"


def test_non_interactive_handlers_raise_login_required(hermes_home):
    handle = build_auth(OAUTH, hermes_home=str(hermes_home), interactive=False)
    with pytest.raises(LoginRequired):
        asyncio.run(handle.httpx_auth().context.redirect_handler("http://x/authorize"))


def test_parse_callback_reads_code_and_state():
    assert parse_callback("http://127.0.0.1:47631/callback?code=c-1&state=s-1") == ("c-1", "s-1")


def test_parse_callback_refuses_an_error_redirect():
    with pytest.raises(LoginFailed, match="access_denied"):
        parse_callback("http://127.0.0.1:47631/callback?error=access_denied&state=s")


def test_refresh_guard_skips_the_fence_while_the_token_is_fresh(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    handle = _oauth_with_expiry(hermes_home, seconds_left=3000)

    async def go():
        async with handle.refresh_guard():
            pass
    asyncio.run(go())
    assert entered == []


def test_refresh_guard_takes_the_fence_inside_the_skew(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    handle = _oauth_with_expiry(hermes_home, seconds_left=10)

    async def go():
        async with handle.refresh_guard():
            pass
    asyncio.run(go())
    assert entered == [1]


def test_refresh_guard_takes_the_fence_when_the_file_records_no_expiry(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    handle = _oauth_with_expiry(hermes_home, seconds_left=None)

    async def go():
        async with handle.refresh_guard():
            pass
    asyncio.run(go())
    assert entered == [1]


class _NoFence:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _oauth_with_expiry(hermes_home, seconds_left):
    from mcp.shared.auth import OAuthToken
    path, _ = token_paths(str(hermes_home))
    path.parent.mkdir(parents=True, exist_ok=True)
    s = FileTokenStorage(path, OAUTH.mcp_url)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=seconds_left, refresh_token="r")))
    return build_auth(OAUTH, hermes_home=str(hermes_home))
```

`redirect_handler` above reads the SDK context's attribute; if the installed SDK names it
differently, keep the public `OAuthAuth.redirect_handler` attribute and assert on that instead,
and say so in `open_risks`.

- [ ] **Step 2: run and watch them fail.**
  `cd client/hermes && ../../.venv-hermes-plugin/bin/python -m pytest tests/test_auth.py tests/test_tokens.py -q`
- [ ] **Step 3: implement `tokens.py`, then `auth.py`.**
- [ ] **Step 4: run until green.** Paste the summary line. The full OAuth flow against a real
  engine runs only in the lead's gate script.

---

## T3. Recall, gate and schemas (sonnet, implementer)

**Files:** `client/hermes/recall.py`, `client/hermes/gate.py`, `client/hermes/schemas.py`,
`client/hermes/tests/test_recall.py`, `client/hermes/tests/test_gate.py`,
`client/hermes/tests/test_schemas.py`.

**Behaviour:** spec §8 and §9, and the lock's docstrings. Additional rules:

- `format_hit`: collapse whitespace in content to single spaces; strip any `<memory-context>` or
  `</memory-context>` tag, case-insensitive; `occurred` shows the first 10 characters of
  `occurred_at` and appears only when present; `source` appears only when present.
- `format_digest` and `format_hits` count the heading toward `max_chars`.
- `compose` joins with `"\n\n"` and drops whole trailing parts rather than cutting one mid-line.
- `to_hermes_schema` copies `inputSchema` into `parameters`, drops top-level `$schema` and `title`,
  and sets `"type": "object"` and `"properties": {}` when absent.
- `write_cache` writes to a temp file and `os.replace`s it; `read_cache` returns None on any error.
- `identity_from_kwargs` turns `user_id` into `str` when it is an int.
- `session_allowed`, checked in this order: a platform in `local_platforms` passes; platform `cron`
  fails; an empty `owner_user_ids` fails; a `chat_type` of `"dm"` passes only when
  `f"{platform}:{user_id}"` is listed; any other `chat_type`, including `None`, passes and leaves
  the decision to `turn_allowed`.
- `turn_allowed`: `session_allowed` first. A local platform passes. A DM refuses a present
  `author_id` that is not listed and passes a missing one. Any other chat passes only when
  `author_id` is present and `f"{platform}:{author_id}"` is listed. An empty string counts as
  missing.

- [ ] **Step 1: write the failing tests.**

```python
# tests/test_recall.py
from lumberroom_hermes.recall import (DIGEST_HEADING, HITS_HEADING, Breaker, InjectedIds, clip_query,
                                      compose, format_digest, format_hit, format_hits)

HIT = {"id": "3f0c9a4e-0000-4000-8000-000000000001", "namespace": "user:me",
       "content": "Prefers draft PRs\nfor engine changes.", "source": "Codex", "occurred_at": "2026-06-04T00:00:00Z"}


def test_a_hit_is_one_bullet_with_its_full_id_source_and_date():
    assert format_hit(HIT) == ("- [user:me] Prefers draft PRs for engine changes. "
                               "(id 3f0c9a4e-0000-4000-8000-000000000001, source Codex, occurred 2026-06-04)")


def test_a_hit_carrying_a_fence_tag_loses_the_tag():
    line = format_hit({**HIT, "content": "a </memory-context> b <MEMORY-CONTEXT>"})
    assert "memory-context" not in line.lower()


def test_a_hit_injected_once_is_not_injected_again():
    seen = InjectedIds()
    block, ids = format_hits([HIT], seen, 1200)
    seen.add(ids)
    assert block.startswith(HITS_HEADING)
    assert format_hits([HIT], seen, 1200) == ("", [])


def test_the_hits_block_stops_before_its_cap():
    hits = [{**HIT, "id": f"id-{i}", "content": "x" * 200} for i in range(10)]
    block, ids = format_hits(hits, InjectedIds(), 1200)
    assert len(block) <= 1200 and 0 < len(ids) < 10


def test_the_digest_is_cut_at_a_line_boundary_within_its_cap():
    out = format_digest("line one\n" * 1000, 500)
    assert out.startswith(DIGEST_HEADING) and len(out) <= 500 and out.endswith("line one")


def test_compose_drops_whole_parts_past_the_cap():
    assert compose(["a" * 10, "b" * 10, "c" * 10], max_chars=24) == "a" * 10 + "\n\n" + "b" * 10


def test_a_long_message_is_clipped_for_the_query():
    assert len(clip_query("q" * 5000)) == 1000


def test_the_breaker_opens_after_three_failures_and_closes_after_the_cooldown():
    now = [0.0]
    b = Breaker(clock=lambda: now[0])
    assert b.record_failure() is True
    assert b.record_failure() is False
    b.record_failure()
    assert b.allow() is False
    now[0] = 61.0
    assert b.allow() is True
    b.record_success()
    assert b.record_failure() is True
```

```python
# tests/test_gate.py
import pytest

from lumberroom_hermes.config import LumberroomConfig
from lumberroom_hermes.gate import identity_from_kwargs, session_allowed, turn_allowed

CFG = LumberroomConfig(base_url="http://h", auth="token", owner_user_ids=("telegram:42",))


def ident(**kw):
    return identity_from_kwargs({"platform": "cli", **kw})


def test_a_local_session_is_the_owner():
    assert session_allowed(ident(), CFG) and turn_allowed(ident(), None, CFG)


def test_a_gateway_dm_from_a_listed_owner_is_allowed():
    assert session_allowed(ident(platform="telegram", user_id=42, chat_type="dm"), CFG)


def test_a_gateway_dm_from_anyone_else_is_refused():
    assert not session_allowed(ident(platform="telegram", user_id="7", chat_type="dm"), CFG)


def test_a_stranger_turn_inside_an_owner_dm_is_refused():
    i = ident(platform="telegram", user_id="42", chat_type="dm")
    assert turn_allowed(i, "42", CFG) and not turn_allowed(i, "7", CFG)


def test_an_owner_dm_turn_without_an_author_is_allowed():
    assert turn_allowed(ident(platform="telegram", user_id="42", chat_type="dm"), None, CFG)


def test_a_listed_owner_turn_in_a_group_chat_is_allowed():
    i = ident(platform="telegram", user_id="7", chat_type="group")
    assert session_allowed(i, CFG) and turn_allowed(i, "42", CFG)


def test_a_non_owner_turn_in_a_group_chat_is_refused():
    i = ident(platform="telegram", user_id="42", chat_type="group")
    assert not turn_allowed(i, "7", CFG)


def test_a_group_turn_without_an_author_is_refused():
    i = ident(platform="telegram", user_id="42", chat_type="group")
    assert not turn_allowed(i, None, CFG) and not turn_allowed(i, "", CFG)


@pytest.mark.parametrize("chat_type", ["guild", "thread", "channel", "webhook", None])
def test_every_shared_chat_admits_only_a_listed_owners_turns(chat_type):
    i = ident(platform="telegram", user_id="7", chat_type=chat_type)
    assert session_allowed(i, CFG) and turn_allowed(i, "42", CFG)
    assert not turn_allowed(i, "7", CFG) and not turn_allowed(i, None, CFG)


def test_a_webhook_route_gets_memory_only_when_the_owner_lists_it():
    i = ident(platform="webhook", user_id="webhook:github", chat_type="webhook")
    listed = LumberroomConfig(base_url="http://h", auth="token", owner_user_ids=("webhook:webhook:github",))
    assert turn_allowed(i, "webhook:github", listed) and not turn_allowed(i, "webhook:github", CFG)


def test_cron_is_the_owner_by_default():
    cron = ident(platform="cron", agent_context="cron")
    assert session_allowed(cron, CFG) and turn_allowed(cron, None, CFG)


def test_removing_cron_from_local_platforms_refuses_cron_even_with_owners_listed():
    no_cron = LumberroomConfig(base_url="http://h", auth="token", owner_user_ids=("telegram:42",),
                               local_platforms=("cli", "tui", "desktop", "acp"))
    assert not session_allowed(ident(platform="cron", agent_context="cron"), no_cron)


@pytest.mark.parametrize("chat_type", ["dm", "group", None])
def test_empty_owner_ids_refuse_every_gateway(chat_type):
    empty = LumberroomConfig(base_url="http://h", auth="token")
    assert not session_allowed(ident(platform="discord", user_id="1", chat_type=chat_type), empty)
```

```python
# tests/test_schemas.py
from lumberroom_hermes.schemas import load_snapshot, read_cache, select, to_hermes_schema, write_cache


def test_the_snapshot_carries_the_default_tools():
    names = {t["name"] for t in load_snapshot()["tools"]}
    assert {"memory_search", "memory_write", "registry_get"} <= names


def test_conversion_moves_input_schema_to_parameters_and_drops_schema_noise():
    out = to_hermes_schema({"name": "memory_write", "description": "d",
                            "inputSchema": {"$schema": "x", "title": "WriteArgs", "type": "object",
                                            "properties": {"content": {"type": "string"}}, "required": ["content"]}})
    assert out == {"name": "memory_write", "description": "d",
                   "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}}


def test_select_keeps_allowlist_order_and_skips_absent_names():
    tools = [{"name": n} for n in ("memory_write", "memory_search")]
    assert [t["name"] for t in select(["memory_search", "memory_forget", "memory_write"], tools)] == ["memory_search", "memory_write"]


def test_the_cache_round_trips_and_a_broken_cache_reads_as_none(tmp_path):
    write_cache(str(tmp_path), {"instructions": "i", "tools": [{"name": "memory_write"}]})
    assert read_cache(str(tmp_path))["tools"] == [{"name": "memory_write"}]
    (tmp_path / "lumberroom" / "tools_cache.json").write_text("{")
    assert read_cache(str(tmp_path)) is None
```

- [ ] **Step 2: run and watch them fail.**
  `cd client/hermes && ../../.venv-hermes-plugin/bin/python -m pytest tests/test_recall.py tests/test_gate.py tests/test_schemas.py -q`
- [ ] **Step 3: implement the three modules.**
- [ ] **Step 4: run until green.** Paste the summary line.

---

## T4. The provider (sonnet, implementer)

**Files:** `client/hermes/provider.py`, `client/hermes/tests/test_provider.py`.

**Interfaces.** Consumes everything in the lock plus T3's modules. `bridge_factory(cfg, auth,
session_id) -> Bridge` and `auth_factory(cfg, hermes_home) -> AuthHandle` default to
`Bridge(cfg, auth, session_id=session_id)` and `build_auth(cfg, hermes_home=hermes_home)`.

**Behaviour:** spec §7 to §10 exactly. Rules the spec leaves to the implementer:

- `initialize` never raises. Every failure lands in `self._inert_reason` (config, auth config) or
  `self._unauthenticated`, and is logged once at WARNING through `logging.getLogger(__name__)`.
- The candidate set is computed once, on the first `get_tool_schemas()` call, from
  `config.load()` if it parses and `schemas.read_cache(get_hermes_home())` or `load_snapshot()`,
  and frozen for the instance.
- Project: `auto` walks up from `kw.get("cwd")` to the first directory holding `.git` and sends
  that absolute path; no `cwd` or no `.git` sends none.
- Nudge counting runs only when `agent_context == "primary"` and the session is allowed.
- The unreachable line counts as a prefetch output; `recall_status` stays None for it.
- Tool error JSON uses the exact texts in spec §10. The host name in the unreachable text is
  `urllib.parse.urlsplit(cfg.origin).netloc`.
- `get_status_config` never touches the network.
- `get_config_schema`, `save_config` and `post_setup` delegate to `wizard`.

- [ ] **Step 1: write the failing tests.** The fake bridge lives in this test file.

```python
import json
import threading

import pytest

from lumberroom_hermes.bridge import CallResult, ToolsListing
from lumberroom_hermes.provider import LumberroomProvider
from lumberroom_hermes.recall import LOGIN_LINE, NUDGE_LINE, UNREACHABLE_LINE
from lumberroom_hermes.schemas import load_snapshot

OK = lambda structured: CallResult("ok", structured, json.dumps(structured), None)  # noqa: E731
HIT = {"id": "11111111-1111-4111-8111-111111111111", "namespace": "user:me", "content": "Likes terse plans.",
       "source": "Codex"}


class FakeBridge:
    def __init__(self, tools=None, fail=None):
        self.calls, self.closed, self.session_ids = [], False, []
        self.tools = tools if tools is not None else [t for t in load_snapshot()["tools"]]
        self.fail = fail
        self.answers = {"context_bootstrap": OK({"text": "- digest line"}),
                        "memory_search": OK({"hits": [HIT]}),
                        "memory_write": OK({"id": "w-1", "namespace": "user:me"})}

    def start(self):
        pass

    def list_tools(self, *, timeout):
        if self.fail:
            return CallResult(self.fail, None, "", self.fail), None
        return CallResult("ok", None, "", None), ToolsListing(tuple(self.tools), "Engine instructions.")

    def call(self, tool, args, *, invocation, timeout):
        self.calls.append((tool, args, invocation, timeout))
        if self.fail:
            return CallResult(self.fail, None, "", "boom")
        return self.answers[tool]

    def call_many(self, calls, *, invocation, timeout):
        return [self.call(t, a, invocation=invocation, timeout=timeout) for t, a in calls]

    def http_json(self, *a, **k):
        raise AssertionError("the provider never posts admin requests")

    def set_session_id(self, sid):
        self.session_ids.append(sid)

    def close(self, *, timeout=2.0):
        self.closed = True


class StaticAuth:
    mode = "token"


@pytest.fixture
def config_yaml(hermes_home, monkeypatch):
    def write(**block):
        cfg = {"memory": {"provider": "lumberroom", "memory_enabled": False, "user_profile_enabled": False,
                          "lumberroom": {"base_url": "http://fake.lumberroom.test", "auth": "token", **block}}}
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)
    write()
    return write


def make(bridge, **kw):
    built = []

    def factory(cfg, auth, sid):
        built.append(sid)
        return bridge
    p = LumberroomProvider(bridge_factory=factory, auth_factory=lambda cfg, home: StaticAuth(), **kw)
    return p, built


def init(p, hermes_home, **kw):
    p.get_tool_schemas()
    p.initialize("s-1", hermes_home=str(hermes_home), **{"platform": "cli", **kw})


def test_initialize_without_cwd_user_id_or_chat_type_does_not_raise(config_yaml, hermes_home):
    p, _ = make(FakeBridge())
    init(p, hermes_home)
    assert p.system_prompt_block()


def test_initialize_reads_the_secret_before_the_loop_thread_starts(config_yaml, hermes_home):
    order = []
    p = LumberroomProvider(bridge_factory=lambda c, a, s: order.append("bridge") or FakeBridge(),
                           auth_factory=lambda c, h: order.append(("auth", threading.get_ident())) or StaticAuth())
    init(p, hermes_home)
    assert order == [("auth", threading.get_ident()), "bridge"]


def test_a_gateway_stranger_gets_no_block_no_recall_no_tools_and_no_connection(config_yaml, hermes_home):
    p, built = make(FakeBridge())
    init(p, hermes_home, platform="telegram", user_id="7", chat_type="dm")
    assert built == []
    assert p.system_prompt_block() == "" and p.get_tool_schemas() == []
    assert p.prefetch("what do I prefer") == ""
    assert "owner" in json.loads(p.handle_tool_call("memory_write", {"content": "c", "namespace": "user:me"}))["error"]


def test_in_a_group_only_a_listed_owners_turn_reaches_the_engine(config_yaml, hermes_home):
    config_yaml(owner_user_ids=["telegram:42"])
    bridge = FakeBridge()
    p, built = make(bridge)
    init(p, hermes_home, platform="telegram", user_id="7", chat_type="group")
    assert built == ["s-1"]
    p.on_turn_start(1, "q", author_id="7")
    assert p.prefetch("what do I prefer") == ""
    assert "owner" in json.loads(p.handle_tool_call("memory_search", {"query": "q"}))["error"]
    p.on_turn_start(2, "q", author_id=None)
    assert p.prefetch("what do I prefer") == ""
    assert bridge.calls == []
    p.on_turn_start(3, "q", author_id="42")
    assert "Likes terse plans." in p.prefetch("what do I prefer")


def test_sync_turn_calls_nothing(config_yaml, hermes_home):
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    p.sync_turn("u", "a", session_id="s-1")
    assert bridge.calls == []


def test_the_first_prefetch_carries_the_digest_and_the_second_does_not(config_yaml, hermes_home):
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    first = p.prefetch("what do I prefer")
    bridge.answers["memory_search"] = OK({"hits": []})
    second = p.prefetch("anything else")
    assert "- digest line" in first and "Likes terse plans." in first
    assert "digest line" not in second
    assert all(inv == "hook" for _, _, inv, _ in bridge.calls)


def test_prefetch_passes_the_configured_bound_to_the_bridge(config_yaml, hermes_home):
    config_yaml(prefetch_timeout_s=1.5)
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    p.prefetch("q")
    assert {t for *_, t in bridge.calls} == {1.5}


def test_a_reset_or_compression_rearms_the_digest(config_yaml, hermes_home):
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    p.prefetch("q")
    p.on_session_switch("s-2", reason="compression")
    assert "- digest line" in p.prefetch("q2")
    assert bridge.session_ids == ["s-2"]


def test_the_first_failure_of_an_outage_says_memory_was_not_checked(config_yaml, hermes_home):
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    bridge.fail = "unreachable"
    assert p.prefetch("q") == UNREACHABLE_LINE
    assert p.prefetch("q") == ""


def test_oauth_without_a_token_marks_unauthenticated_and_says_log_in_once(config_yaml, hermes_home):
    config_yaml(auth="oauth")
    p, _ = make(FakeBridge(fail="login_required"))
    init(p, hermes_home)
    assert p.prefetch("q") == LOGIN_LINE
    assert p.prefetch("q") == ""
    assert "login" in json.loads(p.handle_tool_call("memory_search", {"query": "q"}))["error"]


def test_post_init_schemas_never_add_a_name_the_pre_init_set_lacked(config_yaml, hermes_home):
    config_yaml(tools=["memory_search", "memory_write"])
    extra = [t for t in load_snapshot()["tools"]] + [{"name": "surprise", "description": "", "inputSchema": {}}]
    p, _ = make(FakeBridge(tools=extra))
    before = {s["name"] for s in p.get_tool_schemas()}
    p.initialize("s-1", hermes_home=str(hermes_home), platform="cli")
    after = {s["name"] for s in p.get_tool_schemas()}
    assert after <= before == {"memory_search", "memory_write"}


def test_a_grant_without_memory_forget_hides_it_after_init(config_yaml, hermes_home):
    tools = [t for t in load_snapshot()["tools"] if t["name"] != "memory_forget"]
    p, _ = make(FakeBridge(tools=tools))
    assert "memory_forget" in {s["name"] for s in p.get_tool_schemas()}
    p.initialize("s-1", hermes_home=str(hermes_home), platform="cli")
    assert "memory_forget" not in {s["name"] for s in p.get_tool_schemas()}


def test_a_write_timeout_says_the_call_may_have_taken_effect(config_yaml, hermes_home):
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    bridge.fail = "timeout"
    err = json.loads(p.handle_tool_call("memory_write", {"content": "c", "namespace": "user:me"}))["error"]
    assert "may have taken effect" in err


def test_model_tool_calls_go_out_without_the_hook_invocation(config_yaml, hermes_home):
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    out = json.loads(p.handle_tool_call("memory_write", {"content": "c", "namespace": "user:me"}))
    assert out["id"] == "w-1" and bridge.calls[-1][2] == "model"


def test_the_nudge_appears_after_review_interval_turns_in_a_primary_session_only(config_yaml, hermes_home):
    config_yaml(review_interval=2)
    p, _ = make(FakeBridge())
    init(p, hermes_home)
    p.sync_turn("u", "a")
    p.sync_turn("u", "a")
    assert NUDGE_LINE in p.prefetch("q")
    cron = make(FakeBridge())[0]
    init(cron, hermes_home, platform="cron", agent_context="cron")
    cron.sync_turn("u", "a")
    cron.sync_turn("u", "a")
    out = cron.prefetch("q")
    assert out and NUDGE_LINE not in out


def test_hermes_routes_every_exposed_tool(config_yaml, hermes_home):
    from agent.memory_manager import MemoryManager
    p, _ = make(FakeBridge())
    mm = MemoryManager()
    mm.add_provider(p)
    mm.initialize_all(session_id="s-1", platform="cli", hermes_home=str(hermes_home))
    exposed = {s["name"] for s in mm.get_all_tool_schemas()}
    assert exposed and exposed <= mm.get_all_tool_names()
    mm.shutdown_all()
```

- [ ] **Step 2: run and watch them fail.**
  `cd client/hermes && ../../.venv-hermes-plugin/bin/python -m pytest tests/test_provider.py -q`
- [ ] **Step 3: implement `provider.py`.**
- [ ] **Step 4: run until green.** Paste the summary line.

---

## T5. Setup, import and the CLI (sonnet, implementer)

**Files:** `client/hermes/wizard.py`, `client/hermes/importer.py`, `client/hermes/cli.py`,
`client/hermes/tests/test_wizard.py`, `client/hermes/tests/test_importer.py`,
`client/hermes/tests/test_cli.py`.

**Behaviour:** spec §11 and §12, with these exact texts and shapes.

- Prompts, in this order, each read with one `ask` call:
  1. deployment: `1) Self-hosted engine (enter its URL)` and `2) lumberroom.cloud`, then
     `Choose 1 or 2: `. No default: an empty answer asks again.
  2. self-hosted only: `Engine URL: `, then `Auth mode, token or oauth: `.
  3. hosted only: `1) Sign in with a browser` and `2) Paste an API token (lr_...)`, then `Choose 1 or 2: `.
  4. token mode: the secret, through `ask_secret`, never `ask`.
  5. OAuth mode: `Log in now? [y/N] `.
  6. `Run the live check now? [y/N] `.
  7. only when `MEMORY.md` or `USER.md` holds entries: `Import N entries into the review queue now? [y/N] `.
- `ask_secret` defaults to `getpass.getpass`. Secrets go through
  `hermes_cli.config.save_env_value("LUMBERROOM_HERMES_TOKEN", value)`; config through
  `hermes_cli.config.save_config(config)`.
- `apply_builtin_off` returns lines such as `memory.memory_enabled: true -> false`.
- `importer.proposals_body` builds exactly the spec §11 JSON. `source.file_path` is the absolute
  path. `tags` is `["hermes-import"]`. `import_builtin` opens the run, posts in batches of 100,
  closes the run with `{"entries_seen": n, "proposals_new": x, "proposals_reinforced": y}`, and
  raises `MissingGrant` on any 403 from the ingest routes.
- `cli.register_cli(subparser)`: `subs = subparser.add_subparsers(dest="lumberroom_command")`;
  `login` with `--no-browser`; `logout`; `status` with `--json`; `import-builtin` with `--dry-run`;
  `subparser.set_defaults(func=lumberroom_command)`. `lumberroom_command` returns the exit code in
  spec §12 and prints `Usage: hermes lumberroom <login|logout|status|import-builtin>` with no
  subcommand.
- `status` builds auth with `interactive=False`, starts a `Bridge`, runs `list_tools(timeout=5)`,
  prints one `key: value` line per item (`base_url`, `auth`, `credential`, `built-in store`,
  `reachable`, `tools`, `round trip ms`), and with `--json` prints one JSON object instead.
- `login` reads pasted input with `sys.stdin.readline`.

- [ ] **Step 1: write the failing tests.**

```python
# tests/test_importer.py
import pytest

from lumberroom_hermes.importer import MissingGrant, import_builtin, proposals_body, read_builtin_entries


def seed(home):
    (home / "memories" / "MEMORY.md").write_text("Dev box runs Postgres on 5433.\n§\n\n§\nUse ruff.\n")
    (home / "memories" / "USER.md").write_text("Prefers terse plans.\n")


def test_entries_split_on_the_section_sign_and_land_in_their_namespaces(hermes_home):
    seed(hermes_home)
    got = [(e.file, e.text, e.namespace) for e in read_builtin_entries(str(hermes_home))]
    assert got == [("MEMORY.md", "Dev box runs Postgres on 5433.", "global"), ("MEMORY.md", "Use ruff.", "global"),
                   ("USER.md", "Prefers terse plans.", "user:me")]


def test_the_proposal_body_matches_the_engine_shape(hermes_home):
    seed(hermes_home)
    body = proposals_body(read_builtin_entries(str(hermes_home))[:1], "r-1")
    fact = body["facts"][0]
    assert body["extractor"] == "hermes-builtin-import"
    assert fact["speaker"] == "main_model" and fact["span_text"] == fact["content"]
    assert fact["source"]["run_id"] == "r-1" and len(fact["source"]["entry_uuid"]) == 64
    assert fact["source"]["file_path"].endswith("memories/MEMORY.md")


class AdminBridge:
    def __init__(self, status=200):
        self.status, self.posts = status, []

    def http_json(self, method, path, body, *, timeout):
        self.posts.append((method, path, body))
        if self.status != 200:
            return self.status, {"error": "forbidden"}
        if path == "/admin/ingest/runs":
            return 200, {"run_id": "r-1"}
        if path == "/admin/ingest/proposals":
            return 200, {"proposals_new": len(body["facts"]), "proposals_reinforced": 0, "refused": 0, "blocked": 0, "confirmations": 0, "outcomes": []}
        return 200, {}


def test_import_opens_posts_and_closes_one_run(hermes_home):
    seed(hermes_home)
    bridge = AdminBridge()
    report = import_builtin(bridge, str(hermes_home), profile="default", dry_run=False)
    assert [p[1] for p in bridge.posts] == ["/admin/ingest/runs", "/admin/ingest/proposals", "/admin/ingest/runs/r-1/close"]
    assert report.proposals_new == 3


def test_a_dry_run_posts_nothing(hermes_home):
    seed(hermes_home)
    bridge = AdminBridge()
    assert import_builtin(bridge, str(hermes_home), profile="default", dry_run=True).posted == 0
    assert bridge.posts == []


def test_a_403_is_a_missing_grant(hermes_home):
    seed(hermes_home)
    with pytest.raises(MissingGrant):
        import_builtin(AdminBridge(status=403), str(hermes_home), profile="default", dry_run=False)
```

```python
# tests/test_wizard.py
from lumberroom_hermes.wizard import apply_builtin_off, post_setup


def run_setup(monkeypatch, answers, secret="t-1"):
    saved, env = {}, {}
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: env.update({k: v}))
    it = iter(answers)
    config = {"memory": {}}
    post_setup("/tmp/h", config, ask=lambda _: next(it), ask_secret=lambda _: secret, out=lambda _: None)
    return saved, env


def test_self_hosted_token_setup_writes_the_block_the_secret_and_the_builtin_flags(monkeypatch):
    saved, env = run_setup(monkeypatch, ["1", "http://127.0.0.1:8787", "token", "n"])
    m = saved["memory"]
    assert m["provider"] == "lumberroom" and m["memory_enabled"] is False and m["user_profile_enabled"] is False
    assert m["lumberroom"] == {"base_url": "http://127.0.0.1:8787", "auth": "token"}
    assert env == {"LUMBERROOM_HERMES_TOKEN": "t-1"}


def test_hosted_browser_setup_needs_no_url_and_chooses_oauth(monkeypatch):
    saved, env = run_setup(monkeypatch, ["2", "1", "n", "n"])
    assert saved["memory"]["lumberroom"] == {"base_url": "https://mcp.lumberroom.cloud", "auth": "oauth"}
    assert env == {}


def test_an_empty_deployment_answer_asks_again(monkeypatch):
    saved, _ = run_setup(monkeypatch, ["", "2", "2", "n"])
    assert saved["memory"]["lumberroom"]["auth"] == "token"


def test_apply_builtin_off_reports_each_change():
    config = {"memory": {"memory_enabled": True}}
    lines = apply_builtin_off(config)
    assert "memory.memory_enabled: true -> false" in lines
    assert config["memory"]["provider"] == "lumberroom" and config["memory"]["nudge_interval"] == 0
```

The profile directory `/tmp/h` holds no `memories/`, so prompt 7 never appears in these tests.

```python
# tests/test_cli.py
import argparse

from lumberroom_hermes.cli import lumberroom_command, register_cli


def parser():
    p = argparse.ArgumentParser()
    register_cli(p)
    return p


def test_the_four_subcommands_parse():
    p = parser()
    assert p.parse_args(["login", "--no-browser"]).no_browser is True
    assert p.parse_args(["status", "--json"]).json is True
    assert p.parse_args(["import-builtin", "--dry-run"]).dry_run is True
    assert p.parse_args(["logout"]).lumberroom_command == "logout"


def test_no_subcommand_prints_usage_and_fails(capsys):
    assert lumberroom_command(parser().parse_args([])) == 1
    assert "hermes lumberroom" in capsys.readouterr().out


def test_status_exits_one_when_the_config_is_missing(monkeypatch, capsys):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"memory": {}})
    assert lumberroom_command(parser().parse_args(["status"])) == 1
    assert "base_url" in capsys.readouterr().out
```

- [ ] **Step 2: run and watch them fail.**
  `cd client/hermes && ../../.venv-hermes-plugin/bin/python -m pytest tests/test_wizard.py tests/test_importer.py tests/test_cli.py -q`
- [ ] **Step 3: implement the three modules.**
- [ ] **Step 4: run until green.** Paste the summary line.

---

## T6. The package README (content-writer, sonnet)

**Files:** `client/hermes/README.md`. This file is also what the Hermes catalog page renders at the
pinned SHA (`hermes-agent/plugin-catalog/README.md`, "readme").

**Contract:** sections in this order: what it does (three bullets from spec §1); install (the
directory, symlinked or copied into `~/.hermes/plugins/lumberroom`, which is also how a Hermes
catalog install lands; `pip install lumberroom-hermes` only for owner-managed Hermes builds such as
Nix, marked "after the first release", because Hermes's docs say not to pip-inject into a
PM-managed install, `hermes-agent/website/docs/developer-guide/memory-provider-plugin.md:37-45`); setup for each of the
three rows in spec §6.3; the config table from spec §4; commands from spec §12; the failure table
from spec §10; the owner gate from spec §8, with its two accepted costs, in eight lines; what it does not do (spec §13). Every
number carries "design target" unless it cites a source. Say "implemented", never "works".

**Gate:** `grep -rP '\x{2014}' client/hermes/README.md` prints nothing; the lead reads it against the
spec and checks every command against `cli.py`.

---

## W. The wiring pass (lead)

- [ ] **W1: `tests/test_contract.py`.** Hermes loads the directory, routes the tools, and every
  Hermes import the plugin uses exists.

```python
import importlib
import os

import pytest

from conftest import PLUGIN_DIR


@pytest.mark.parametrize("module, name", [
    ("agent.memory_provider", "MemoryProvider"), ("agent.memory_provider", "RecallStatus"),
    ("agent.memory_provider", "spawn_context_thread"), ("agent.secret_scope", "get_secret"),
    ("hermes_cli.config", "load_config"), ("hermes_cli.config", "save_config"),
    ("hermes_cli.config", "save_env_value"), ("hermes_constants", "get_hermes_home"),
])
def test_every_hermes_symbol_the_plugin_imports_exists(module, name):
    assert hasattr(importlib.import_module(module), name)


def test_hermes_loads_the_directory_provider_and_its_cli(hermes_home, monkeypatch):
    (hermes_home / "plugins").mkdir()
    os.symlink(PLUGIN_DIR, hermes_home / "plugins" / "lumberroom")
    (hermes_home / "config.yaml").write_text(
        "memory:\n  provider: lumberroom\n  lumberroom:\n    base_url: http://fake.lumberroom.test\n    auth: oauth\n")
    from plugins.memory import discover_plugin_cli_commands, load_memory_provider
    provider = load_memory_provider("lumberroom")
    assert provider is not None and provider.name == "lumberroom" and provider.is_available()
    commands = discover_plugin_cli_commands()
    assert commands and commands[0]["name"] == "lumberroom" and commands[0]["handler_fn"] is not None
```

- [ ] **W2: the full unit suite.**
  `cd client/hermes && ../../.venv-hermes-plugin/bin/python -m pytest -q` ends with 0 failed.
- [ ] **W3: `scripts/lib/hermes_plugin_drive.py`.**

```python
"""Drive the lumberroom provider through Hermes's own loader and MemoryManager.

Used by scripts/hermes-plugin-test.sh inside the plugin venv, with HERMES_HOME set to a throwaway
profile. No model is involved: these are the calls a Hermes turn makes.
"""

import argparse
import json
import os
import sys
from pathlib import Path


def fact(nonce: str) -> str:
    return f"The Hermes plugin gate nickname is HERMESLARK-{nonce}."


def manager(session: str, **kwargs):
    from agent.memory_manager import MemoryManager
    from agent.secret_scope import build_profile_secret_scope, set_secret_scope
    from plugins.memory import load_memory_provider

    home = os.environ["HERMES_HOME"]
    # A real Hermes process installs the profile's .env as the secret scope before any hook runs.
    set_secret_scope(build_profile_secret_scope(Path(home)), profile_home=home)
    provider = load_memory_provider("lumberroom")
    if provider is None:
        sys.exit("load_memory_provider('lumberroom') returned None")
    if not provider.is_available():
        sys.exit(f"provider unavailable: {provider.unavailable_reason()}")
    mm = MemoryManager()
    mm.add_provider(provider)
    mm.initialize_all(session_id=session, **{"platform": "cli", **kwargs})
    return mm


def write(a) -> int:
    mm = manager(a.session)
    try:
        if "memory_write" not in mm.get_all_tool_names():
            sys.exit(f"memory_write is not routed: {sorted(mm.get_all_tool_names())}")
        out = json.loads(mm.handle_tool_call("memory_write", {"content": fact(a.nonce), "namespace": "user:me"}))
        if "error" in out:
            sys.exit(f"write refused: {out['error']}")
        print(json.dumps({"id": out.get("id")}))
        return 0
    finally:
        mm.shutdown_all()


def recall(a) -> int:
    from agent.memory_manager import build_memory_context_block

    mm = manager(a.session)
    try:
        raw = mm.prefetch_all(f"What is the Hermes plugin gate nickname HERMESLARK-{a.nonce}?", session_id=a.session)
        block = build_memory_context_block(raw)
        print(block)
        return 0 if f"HERMESLARK-{a.nonce}" in block else 1
    finally:
        mm.shutdown_all()


def gated(a) -> int:
    mm = manager(a.session, platform="telegram", user_id="999000", chat_type="dm")
    try:
        if mm.get_all_tool_schemas():
            sys.exit("a gated session exposed tools")
        if mm.prefetch_all(f"HERMESLARK-{a.nonce}", session_id=a.session):
            sys.exit("a gated session recalled memory")
        out = json.loads(mm.handle_tool_call("memory_write", {"content": "x", "namespace": "user:me"}))
        if "owner" not in out.get("error", ""):
            sys.exit(f"a gated write was not refused: {out}")
        print("gated ok")
        return 0
    finally:
        mm.shutdown_all()


def entry_point(_a) -> int:
    from plugins.memory import find_provider_dir, find_provider_entry_point, load_memory_provider

    if find_provider_entry_point("lumberroom") is None:
        sys.exit("no hermes_agent.memory_providers entry point named lumberroom")
    directory = find_provider_dir("lumberroom")
    provider = load_memory_provider("lumberroom")
    if provider is None or directory is None or not (directory / "cli.py").exists():
        sys.exit(f"entry point load failed: provider={provider} dir={directory}")
    print(f"entry point ok: {directory}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["write", "recall", "gated", "entry-point"])
    ap.add_argument("--nonce", default="")
    ap.add_argument("--session", default="hpt")
    a = ap.parse_args()
    return {"write": write, "recall": recall, "gated": gated, "entry-point": entry_point}[a.command](a)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **W4: `scripts/hermes-plugin-test.sh`.** Same house shape as `scripts/oauth-flow-test.sh`:
  bash, `set -euo pipefail`, scratch servers through `scripts/lib/scratch-server.sh`, a nonce per
  run, coloured PASS and FAIL lines, non-zero exit on any failure, a summary.

```bash
#!/usr/bin/env bash
# Proves the lumberroom memory provider for Hermes Agent against real engines, through Hermes's own
# plugin loader, MemoryManager and CLI: token mode first, then AUTH_MODE=oauth.
#
#   POSTGRES_PASSWORD=... ./scripts/hermes-plugin-test.sh
#   ./scripts/hermes-plugin-test.sh --hermes-src ~/work/open-source/hermes-agent --keep
#   ./scripts/hermes-plugin-test.sh --capture     rewrite the tools snapshot and the test transcript, then exit
#
# Two scratch servers, never 8787: token mode on 8796 against lumberroom_hermes_plugin_test, OAuth
# on 8797 against lumberroom_hermes_plugin_oauth_test. Both databases drop on exit unless --keep.
# The plugin venv is .venv-hermes-plugin, holding Hermes installed from --hermes-src.
#
# What this does NOT prove: that a model calls memory_write. Every step drives Hermes's
# MemoryManager, the object a real turn calls, with no model in the loop, because a model turn
# bills a provider and would measure the model. HERMES_PLUGIN_LIVE_TURN=1 adds one `hermes -z`
# turn at the end for the owner to run by hand; it needs a configured model provider and costs money.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_SRC="${HERMES_AGENT_SRC:-$HOME/work/open-source/hermes-agent}"
TOKEN_PORT=8796
OAUTH_PORT=8797
KEEP=0
CAPTURE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --hermes-src) HERMES_SRC="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --capture) CAPTURE=1; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1. See --help." >&2; exit 1 ;;
  esac
done

for bin in docker curl openssl; do
  command -v "$bin" >/dev/null 2>&1 || { echo "$bin is required" >&2; exit 1; }
done
PYTHON="${HERMES_PLUGIN_PYTHON:-python3.14}"
VENV="${HERMES_PLUGIN_VENV:-$REPO_DIR/.venv-hermes-plugin}"
PY="$VENV/bin/python"
HERMES="$VENV/bin/hermes"
DRIVE="$REPO_DIR/scripts/lib/hermes_plugin_drive.py"
PLUGIN_DIR="$REPO_DIR/client/hermes"
WORK="$(mktemp -d)"
NONCE="$(openssl rand -hex 4)"

FAILED=0
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILED=1; }
die() { fail "$*"; printf '\nhermes-plugin-test FAILED\n'; exit 1; }

ensure_venv() {
  if [ ! -x "$PY" ]; then
    [ -d "$HERMES_SRC" ] || die "no Hermes checkout at $HERMES_SRC (pass --hermes-src)"
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/pip" install --quiet "$HERMES_SRC[mcp]"
    "$VENV/bin/pip" install --quiet "mcp>=2.0.0,<3" "httpx2>=2.7.0,<3" "filelock>=3.12,<4" \
      "pytest==9.1.1" "pytest-asyncio==1.3.0"
  fi
  "$PY" -c "import agent.memory_provider, plugins.memory, mcp, httpx2, filelock" \
    || die "the plugin venv at $VENV cannot import Hermes or the plugin's dependencies"
  pass "plugin venv ready (Hermes at $(git -C "$HERMES_SRC" rev-parse --short HEAD 2>/dev/null || echo unknown))"
}

psql_q() {
  docker compose -f "$REPO_DIR/docker-compose.yml" exec -T db \
    psql -U "${POSTGRES_USER:-lumberroom}" -d "$1" -tAc "$2"
}

make_home() {
  # make_home DIR BASE_URL AUTH
  mkdir -p "$1/plugins" "$1/memories"
  ln -s "$PLUGIN_DIR" "$1/plugins/lumberroom"
  cat >"$1/config.yaml" <<YAML
memory:
  provider: lumberroom
  memory_enabled: false
  user_profile_enabled: false
  nudge_interval: 0
  lumberroom:
    base_url: $2
    auth: $3
YAML
}

wait_ready() {
  local i=0
  until curl -sf "$1/readyz" >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -ge 90 ] && return 1; sleep 2
  done
}

drive() { HERMES_HOME="$1" "$PY" "$DRIVE" "${@:2}"; }

trap 'status=$?; scratch_stop 2>/dev/null || true; rm -rf "$WORK"; exit $status' EXIT INT TERM

say "1/13 plugin venv"
ensure_venv

say "2/13 scratch engine in token mode"
TOKEN="$(openssl rand -hex 32)"
SCRATCH_DB=lumberroom_hermes_plugin_test
SCRATCH_NAME="${LUMBERROOM_HERMES_PLUGIN_TEST_SERVER:-lumberroom-hermes-plugin-test-server}"
SCRATCH_PORT=$TOKEN_PORT
SCRATCH_KEEP=$KEEP
SCRATCH_TOKENS="[{\"client\":\"hermes-plugin-test\",\"token\":\"$TOKEN\",\"read\":[{\"namespace\":\"*\",\"max\":\"private\"}],\"write\":[\"user:me\",\"global\",\"project:*\"],\"mayIngest\":true,\"mayDelete\":true}]"
export SCRATCH_DB SCRATCH_NAME SCRATCH_PORT SCRATCH_KEEP SCRATCH_TOKENS
# shellcheck source=lib/scratch-server.sh
. "$REPO_DIR/scripts/lib/scratch-server.sh"
scratch_start || die "the token-mode scratch engine did not start"
URL="$SCRATCH_URL"
pass "engine ready at $URL"

if [ "$CAPTURE" -eq 1 ]; then
  "$PY" "$REPO_DIR/scripts/lib/hermes_plugin_capture.py" --url "$URL" --token "$TOKEN" \
    --snapshot "$PLUGIN_DIR/tools_snapshot.json" \
    --transcript "$PLUGIN_DIR/tests/fixtures/engine_transcript.json"
  exit 0
fi

say "3/13 the checked-in tools snapshot matches the live engine"
"$PY" "$REPO_DIR/scripts/lib/hermes_plugin_capture.py" --url "$URL" --token "$TOKEN" \
  --snapshot "$WORK/snapshot.json" --transcript "$WORK/transcript.json" >/dev/null
if "$PY" - "$PLUGIN_DIR/tools_snapshot.json" "$WORK/snapshot.json" <<'PY'
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:3])
key = lambda s: (s["instructions"], sorted(json.dumps(t, sort_keys=True) for t in s["tools"]))
sys.exit(0 if key(a) == key(b) else 1)
PY
then pass "snapshot matches"; else fail "snapshot drifted from the engine: rerun with --capture and review the diff"; fi

say "4/13 a throwaway HERMES_HOME with the plugin symlinked in"
HOME_T="$WORK/home-token"
make_home "$HOME_T" "$URL" token
printf 'LUMBERROOM_HERMES_TOKEN=%s\n' "$TOKEN" >"$HOME_T/.env"
chmod 600 "$HOME_T/.env"
pass "profile at $HOME_T"

say "5/13 hermes lumberroom status reaches the engine"
if HERMES_HOME="$HOME_T" "$HERMES" lumberroom status >"$WORK/status.out" 2>&1 \
   && grep -q memory_write "$WORK/status.out"; then
  pass "status lists memory_write"
else
  fail "status failed: $(tail -5 "$WORK/status.out")"
fi

say "6/13 a nonce written through MemoryManager.handle_tool_call"
drive "$HOME_T" write --nonce "$NONCE" --session "hpt-a-$NONCE" >"$WORK/write.out" 2>&1 \
  && pass "write accepted: $(cat "$WORK/write.out")" || fail "write failed: $(cat "$WORK/write.out")"

say "7/13 the engine restarts, and a fresh session recalls the nonce through prefetch_all"
docker restart "$SCRATCH_NAME" >/dev/null && wait_ready "$URL" || die "the engine did not come back after a restart"
drive "$HOME_T" recall --nonce "$NONCE" --session "hpt-b-$NONCE" >"$WORK/recall.out" 2>&1 \
  && pass "the <memory-context> block carries HERMESLARK-$NONCE" \
  || fail "recall missed the nonce: $(head -c 600 "$WORK/recall.out")"

say "8/13 tool_calls: the write counts as the model's, recall as the hook's, each with its session"
ROWS="$(psql_q "$SCRATCH_DB" "SELECT tool || ':' || coalesce(unprompted::text, 'null') || ':' || coalesce(session_id, '') FROM tool_calls WHERE client = 'hermes-plugin-test'")"
for want in "memory_write:true:hpt-a-$NONCE" "memory_search:false:hpt-b-$NONCE" "context_bootstrap:false:hpt-b-$NONCE"; do
  printf '%s\n' "$ROWS" | grep -qx "$want" && pass "row $want" || fail "no row $want in: $(printf '%s' "$ROWS" | tr '\n' ' ')"
done

say "9/13 a gateway stranger gets nothing, and nothing reaches the engine"
drive "$HOME_T" gated --nonce "$NONCE" --session "hpt-g-$NONCE" >"$WORK/gated.out" 2>&1 \
  && pass "gated session refused" || fail "gate leaked: $(cat "$WORK/gated.out")"
N="$(psql_q "$SCRATCH_DB" "SELECT count(*) FROM tool_calls WHERE session_id = 'hpt-g-$NONCE'")"
[ "$N" = 0 ] && pass "no tool call recorded for the gated session" || fail "$N tool calls reached the engine from a gated session"

say "10/13 import-builtin fills the queue and never the store, and a rerun adds nothing"
printf 'The gate import fact IMPORTLARK-%s one.\n§\nThe gate import fact IMPORTLARK-%s two.\n' "$NONCE" "$NONCE" >"$HOME_T/memories/MEMORY.md"
printf 'The owner gate import fact IMPORTLARK-%s three.\n' "$NONCE" >"$HOME_T/memories/USER.md"
HERMES_HOME="$HOME_T" "$HERMES" lumberroom import-builtin >"$WORK/import.out" 2>&1 || fail "import-builtin failed: $(cat "$WORK/import.out")"
HERMES_HOME="$HOME_T" "$HERMES" lumberroom import-builtin >>"$WORK/import.out" 2>&1 || fail "the second import-builtin failed"
P="$(psql_q "$SCRATCH_DB" "SELECT count(*) FROM ingest_proposal WHERE content LIKE '%IMPORTLARK-$NONCE%' AND speaker = 'main_model'")"
M="$(psql_q "$SCRATCH_DB" "SELECT count(*) FROM memory WHERE content LIKE '%IMPORTLARK-$NONCE%'")"
[ "$P" = 3 ] && pass "three proposals, speaker main_model" || fail "expected 3 proposals, found $P"
[ "$M" = 0 ] && pass "no live memory holds an imported entry" || fail "$M imported entries reached the live store"

say "11/13 the wheel carries the package and loads through the entry point"
"$VENV/bin/pip" wheel --quiet --no-deps -w "$WORK/dist" "$PLUGIN_DIR"
WHEEL="$(ls "$WORK"/dist/lumberroom_hermes-*.whl)"
"$PY" -m zipfile -l "$WHEEL" >"$WORK/wheel.txt"
if grep -q 'lumberroom_hermes/__init__.py' "$WORK/wheel.txt" && grep -q 'lumberroom_hermes/cli.py' "$WORK/wheel.txt" \
   && grep -q 'lumberroom_hermes/tools_snapshot.json' "$WORK/wheel.txt" && ! grep -q 'tests/' "$WORK/wheel.txt"; then
  pass "wheel layout"
else
  fail "wheel layout: $(cat "$WORK/wheel.txt")"
fi
"$VENV/bin/pip" install --quiet --no-deps --target "$WORK/wheelsite" "$WHEEL"
HOME_E="$WORK/home-entry"
make_home "$HOME_E" "$URL" token
rm "$HOME_E/plugins/lumberroom"
cp "$HOME_T/.env" "$HOME_E/.env"
PYTHONPATH="$WORK/wheelsite" drive "$HOME_E" entry-point >"$WORK/ep.out" 2>&1 \
  && pass "$(cat "$WORK/ep.out")" || fail "entry point: $(cat "$WORK/ep.out")"
scratch_stop

say "12/13 scratch engine in AUTH_MODE=oauth; login through the CLI's paste path"
PASSWORD="$(openssl rand -hex 20)"
SCRATCH_DB=lumberroom_hermes_plugin_oauth_test
SCRATCH_NAME="${LUMBERROOM_HERMES_PLUGIN_OAUTH_SERVER:-lumberroom-hermes-plugin-oauth-server}"
SCRATCH_PORT=$OAUTH_PORT
SCRATCH_TOKENS='[]'
export SCRATCH_DB SCRATCH_NAME SCRATCH_PORT SCRATCH_TOKENS
# Copied from scripts/oauth-flow-test.sh scratch_start_oauth, with one addition: a 75-second access
# token, so step 13 can reach the refresh window without waiting an hour.
scratch_start_oauth() {
  SCRATCH_REPO_DIR="${SCRATCH_REPO_DIR:-$REPO_DIR}"
  SCRATCH_NETWORK="${LUMBERROOM_DOCKER_NETWORK:-lumberroom_default}"
  SCRATCH_PG_USER="${POSTGRES_USER:-lumberroom}"
  scratch_require || return 1
  scratch_compose up -d db >/dev/null
  scratch_compose exec -T -e PGOPTIONS="-c client_min_messages=warning" db \
    psql -U "$SCRATCH_PG_USER" -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH_DB" >/dev/null
  scratch_compose exec -T db psql -U "$SCRATCH_PG_USER" -d postgres -c "CREATE DATABASE $SCRATCH_DB" >/dev/null
  local hash
  hash="$(printf '%s\n' "$PASSWORD" | docker run --rm -i lumberroom-server:0.4.0 lumberroom-server hash-password)" || return 1
  docker rm -f "$SCRATCH_NAME" >/dev/null 2>&1 || true
  docker run -d --name "$SCRATCH_NAME" --network "$SCRATCH_NETWORK" \
    -p "127.0.0.1:${SCRATCH_PORT}:${SCRATCH_PORT}" \
    -e PORT="$SCRATCH_PORT" -e HOST=0.0.0.0 -e TENANT_ID=scratch \
    -e DATABASE_URL="postgres://${SCRATCH_PG_USER}:${POSTGRES_PASSWORD}@db:5432/${SCRATCH_DB}" \
    -e PUBLIC_URL="http://127.0.0.1:${SCRATCH_PORT}" \
    -e AUTH_MODE=oauth -e OWNER_PASSWORD_HASH="$hash" -e OAUTH_COOKIE_SECRET="$(openssl rand -hex 32)" \
    -e OAUTH_ACCESS_TTL_SECS=75 \
    -e EMBED_PROVIDER=hash -e EMBED_DIM=768 -e KEK_PROVIDER=none \
    lumberroom-server:0.4.0 >/dev/null
  SCRATCH_URL="http://127.0.0.1:${SCRATCH_PORT}"
  wait_ready "$SCRATCH_URL"
}
scratch_start_oauth || die "the OAuth scratch engine did not start"
URL="$SCRATCH_URL"
HOME_O="$WORK/home-oauth"
make_home "$HOME_O" "$URL" oauth

mkfifo "$WORK/paste"
HERMES_HOME="$HOME_O" "$HERMES" lumberroom login --no-browser <"$WORK/paste" >"$WORK/login.out" 2>&1 &
LOGIN_PID=$!
exec 7>"$WORK/paste"
AUTH_URL=""
for _ in $(seq 1 30); do
  AUTH_URL="$(grep -o "http://127.0.0.1:${OAUTH_PORT}/oauth/authorize?[^[:space:]]*" "$WORK/login.out" | head -1 || true)"
  [ -n "$AUTH_URL" ] && break
  sleep 1
done
[ -n "$AUTH_URL" ] || die "login printed no authorize URL: $(cat "$WORK/login.out")"
param() { "$PY" -c 'import sys, urllib.parse as u; print(u.parse_qs(u.urlsplit(sys.argv[1]).query).get(sys.argv[2], [""])[0])' "$AUTH_URL" "$1"; }
FORM=()
for k in client_id redirect_uri code_challenge code_challenge_method response_type state resource scope; do
  v="$(param "$k")"; [ -n "$v" ] && FORM+=(--data-urlencode "$k=$v")
done
curl -sS -o "$WORK/login.html" -D "$WORK/login.h" -X POST "$URL/oauth/login" "${FORM[@]}" --data-urlencode "password=$PASSWORD"
COOKIE="$(sed -n 's/^[Ss]et-[Cc]ookie: \(lumberroom_owner=[^;]*\).*/\1/p' "$WORK/login.h" | head -1)"
CSRF="$(grep -o 'name="csrf" value="[^"]*"' "$WORK/login.html" | sed 's/.*value="//; s/"$//' || true)"
if [ -z "$CSRF" ]; then
  curl -sS -o "$WORK/consent.html" -H "Cookie: $COOKIE" "$AUTH_URL"
  CSRF="$(grep -o 'name="csrf" value="[^"]*"' "$WORK/consent.html" | sed 's/.*value="//; s/"$//' || true)"
fi
[ -n "$CSRF" ] || die "no consent screen after owner login"
curl -sS -o /dev/null -D "$WORK/consent.h" -X POST "$URL/oauth/consent" -H "Cookie: $COOKIE" \
  "${FORM[@]}" --data-urlencode "csrf=$CSRF" --data-urlencode "profile=full" --data-urlencode "action=allow"
LOCATION="$(sed -n 's/^[Ll]ocation: \(.*\)\r$/\1/p' "$WORK/consent.h" | head -1)"
[ -n "$LOCATION" ] || die "consent returned no redirect"
printf '%s\n' "$LOCATION" >&7
exec 7>&-
if wait "$LOGIN_PID"; then pass "hermes lumberroom login stored a token pair"; else fail "login failed: $(tail -5 "$WORK/login.out")"; fi
HERMES_HOME="$HOME_O" "$HERMES" lumberroom status >"$WORK/ostatus.out" 2>&1 && grep -q memory_write "$WORK/ostatus.out" \
  && pass "status over OAuth lists memory_write" || fail "status over OAuth: $(tail -5 "$WORK/ostatus.out")"
drive "$HOME_O" write --nonce "$NONCE" --session "hpt-oa-$NONCE" >"$WORK/owrite.out" 2>&1 \
  && pass "OAuth write accepted" || fail "OAuth write: $(cat "$WORK/owrite.out")"
drive "$HOME_O" recall --nonce "$NONCE" --session "hpt-ob-$NONCE" >"$WORK/orecall.out" 2>&1 \
  && pass "OAuth recall carries the nonce" || fail "OAuth recall: $(head -c 600 "$WORK/orecall.out")"

say "13/13 two processes inside the refresh window refresh once, and nobody is logged out"
sleep 20
( drive "$HOME_O" recall --nonce "$NONCE" --session "hpt-r1-$NONCE" >"$WORK/r1.out" 2>&1; echo $? >"$WORK/r1.rc" ) &
( drive "$HOME_O" recall --nonce "$NONCE" --session "hpt-r2-$NONCE" >"$WORK/r2.out" 2>&1; echo $? >"$WORK/r2.rc" ) &
wait
[ "$(cat "$WORK/r1.rc")" = 0 ] && [ "$(cat "$WORK/r2.rc")" = 0 ] \
  && pass "both processes recalled after the refresh" || fail "a process failed across the refresh: $(cat "$WORK/r1.out" "$WORK/r2.out" | head -c 800)"
REPLAYS="$(docker logs "$SCRATCH_NAME" 2>&1 | grep -c 'refresh token replayed' || true)"
[ "$REPLAYS" = 0 ] && pass "no refresh token was replayed" || fail "the engine saw $REPLAYS refresh replays and revoked the family"

if [ "${HERMES_PLUGIN_LIVE_TURN:-0}" = 1 ]; then
  say "live turn (owner opt-in; bills the model provider in this shell's environment)"
  HERMES_HOME="$HOME_O" "$HERMES" -z "What is the Hermes plugin gate nickname? Answer with the nickname only." \
    >"$WORK/live.out" 2>&1 || true
  grep -q "HERMESLARK-$NONCE" "$WORK/live.out" && pass "a real turn answered from memory" || fail "live turn: $(tail -5 "$WORK/live.out")"
fi

printf '\n'
if [ "$FAILED" -eq 0 ]; then printf 'hermes-plugin-test PASSED\n'; else printf 'hermes-plugin-test FAILED\n'; exit 1; fi
```

- [ ] **W5: run the gate.**
  `POSTGRES_PASSWORD=<value> ./scripts/hermes-plugin-test.sh` must end `hermes-plugin-test PASSED`
  with no FAIL line. Paste the output into the PR description. If `hermes lumberroom status` cannot
  start without a model provider configured in the throwaway profile, add the smallest config key
  that lets the CLI boot and record it in the script's header comment.
- [ ] **W6: docs outside the package.** `CHANGELOG.md` gets an Unreleased entry naming
  `client/hermes/` and the gate; `README.md`'s client list gains Hermes with a link to
  `client/hermes/README.md`; `CONTRIBUTING.md`'s acceptance gates list gains
  `./scripts/hermes-plugin-test.sh     # the Hermes plugin recalls what it wrote, in token and OAuth mode`.
- [ ] **W7: em dash sweep.**
  `grep -rP '\x{2014}' client/hermes scripts/hermes-plugin-test.sh scripts/lib/hermes_plugin_*.py docs/specs/hermes-plugin*.md CHANGELOG.md README.md CONTRIBUTING.md`
  prints nothing.

---

## R1. Whole-branch review (opus, reviewer)

Read-only. Brief: the spec, this plan, the diff against `main`. Check, with file:line:

1. Every hook in spec §7 against `provider.py`, and each failure row in spec §10.
2. The routing-table rule: no path returns a name after `initialize` that the pre-init call lacked.
3. No `get_secret` call off the calling thread; no bare `threading.Thread`.
4. The fence: the lock brackets the refresh request, is released on every exit path including
   cancellation, and never holds across a call that does not refresh.
5. No write path besides the model's `memory_write`; `sync_turn` touches no network.
6. The gate: a refused session opens no connection; a refused turn in an allowed shared chat, or
   one with no author, sends nothing to the engine.
7. Prose rules on comments and docstrings.

Return `{"findings": [{"severity", "file", "line", "claim", "evidence", "fix"}], "overall"}`. The
lead checks each claim against the code before acting on it.

---

## Stacked PRs, merge order

All in `the-cybersapien/lumberroom`. Each PR carries the gate output it ran.

1. `feat/hermes-plugin` (this branch): the spec, this plan, decision 0021, the decisions index row,
   and the corrected Hermes claims in `client/hermes-notes.md`, `docs/connect-agents-md.md` and
   `docs/research/client-capabilities.md`. Also the research note and designs JSON it cites.
2. `feat/hermes-plugin-lock`: L0 and L1.
3. `feat/hermes-plugin-transport`: T1 and T2.
4. `feat/hermes-plugin-provider`: T3 and T4.
5. `feat/hermes-plugin-cli`: T5.
6. `feat/hermes-plugin-gate`: W and T6, with `scripts/hermes-plugin-test.sh` output pasted.

Merge 1, 2, 3, 4, 5, 6 in that order; each targets the one before it. lumberroom-cloud receives
`client/hermes/` through `scripts/merge-upstream.sh` with no divergence entry.

## After the merge, owner only

No task performs these. Each needs the owner's explicit approval under his cost and publishing rules.

- Tag an engine release.
- Publish `lumberroom-hermes` to PyPI.
- Open `plugin-catalog/lumberroom.yaml` in NousResearch/hermes-agent: `repo:
  https://github.com/the-cybersapien/lumberroom`, `subdir: client/hermes`, the 40-hex SHA,
  `category: memory`, `tier: community`, `maintainer: the-cybersapien`, `requires_hermes` set to the
  version the gate ran against, `capabilities.requires_env: []`.
- Run the hosted acceptance in spec §14 by hand.
- A GitHub Actions job for the plugin's unit tests, if wanted.
- The Hermes connect page on lumberroom-web.

## Open questions for the owner

- **Q1. Hosted API tokens. Decided 25 September 2026:** keep what the plan builds. Hosted setup
  offers "Sign in with a browser" first and "Paste an API token (lr_...)" second (spec §2, decision
  9). The question as asked: lumberroom.cloud accepts `lr_` API tokens in every auth mode
  (lumberroom-cloud `src/adapters/auth/api_token.rs`), though the brief said hosted is OAuth only.
  The plugin supports both without extra code. Should hosted setup offer "Paste an API token" as
  its second choice, as this plan builds it, or show browser sign-in only?
- **Q2. Shared chats and cron. Decided 25 September 2026:** a listed owner gets memory in gateway
  group, guild, thread, channel and webhook chats as well as DMs; in a shared chat a turn gets
  recall and tools only when its `author_id` is present and listed, and a missing `author_id`
  refuses the turn. An empty `owner_user_ids` still refuses every gateway session. Recall replaying
  to other room members through the shared transcript is an accepted cost. Cron gets memory by
  default: `DEFAULT_LOCAL_PLATFORMS` is `("cli", "tui", "desktop", "acp", "cron")`, and an owner
  removes `cron` from `local_platforms` to turn it off. Spec §8 carries the rules. The question as
  asked: this plan refused every non-DM gateway chat even for a listed owner, because recall
  injected into the owner's turn replays to every later turn of the shared transcript, and it left
  `cron` out of the default local platforms. Confirm both, or name the chats and cron jobs that
  should get memory.
- **Q3. api_server authors.** Raised 25 September 2026 while folding in Q2. An `api_server` turn's
  author comes from the request body (`hermes-agent/gateway/platforms/api_server.py:669-677`), and
  Hermes documents it as a label that grants nothing (`:4028`). This plan adds no special case: an
  `api_server:` entry in `owner_user_ids` trusts any caller holding the API server key, and without
  one every shared-chat `api_server` turn is refused. Should `config.parse` reject `api_server:`
  entries instead?
  Decided 25 September 2026 by the lead as the safe default, open to the owner's override:
  `config.parse` rejects `api_server:` entries with a `ConfigError`, so an `api_server` session
  never counts as the owner. Test: `test_an_api_server_owner_entry_is_refused`.

## Self-review

- Spec coverage: §1 to §13 map to L1 and T1 to T6; §14's three layers map to the per-task tests,
  W1, and W3 to W5. §6.2's three SDK gaps each have a test in T2 and a gate step (13).
- Every public name used in a later task appears in the L1 stubs with the same signature.
- The Review Focus items each name their test.
- Q1 and Q2 are decided. Spec §8's two-level gate has tests in T3 `test_gate.py` and T4
  `test_in_a_group_only_a_listed_owners_turn_reaches_the_engine`; the gate script covers only the
  refused gateway DM.
- No task touches another's file; shared files (`pyproject.toml`, `__init__.py`, `plugin.yaml`,
  `conftest.py`, `fakes.py`, the gate script, root docs) belong to L1 or W.

"""The Hermes MemoryProvider. Hooks only: I/O lives in the bridge, formatting in recall."""

from __future__ import annotations

import functools
import json
import logging
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from agent.memory_provider import MemoryProvider, RecallStatus

from . import config
from . import gate
from . import recall
from . import schemas
from . import wizard
from .auth import AuthHandle, build_auth, token_present
from .bridge import Bridge
from .config import LumberroomConfig

BridgeFactory = Callable[[LumberroomConfig, AuthHandle, str], Bridge]
AuthFactory = Callable[[LumberroomConfig, str], AuthHandle]

logger = logging.getLogger(__name__)

# Tools/list plus server instructions bound at initialize, a design target (spec section 7).
_INIT_TIMEOUT_S = 2.0
_LOGIN_REQUIRED_ERROR = "lumberroom is not logged in. Run hermes lumberroom login."


@functools.lru_cache(maxsize=1)
def _known_tool_names() -> frozenset[str]:
    """Every name this provider could ever expose, independent of config or the live grant.

    Kept apart from the runtime candidate set so a bad config still reports its own reason
    for a real tool name instead of "unknown tool" (spec section 10, "config invalid").
    """
    try:
        names = frozenset(t["name"] for t in schemas.load_snapshot()["tools"])
    except (OSError, ValueError, KeyError):
        names = frozenset()
    return names | frozenset(config.DEFAULT_TOOLS)


def _unreachable_tail(tool_name: str) -> str:
    if tool_name == "memory_write":
        return "Nothing was stored."
    if tool_name == "memory_forget":
        return "Nothing was deleted."
    return "No memory was read."


class LumberroomProvider(MemoryProvider):
    def __init__(self, *, bridge_factory: BridgeFactory | None = None,
                 auth_factory: AuthFactory | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._bridge_factory = bridge_factory or (lambda cfg, auth, sid: Bridge(cfg, auth, session_id=sid))
        self._auth_factory = auth_factory or (lambda cfg, home: build_auth(cfg, hermes_home=home))
        self._clock = clock

        # The candidate set: computed once, on the first get_tool_schemas() call, and frozen.
        self._candidate_computed = False
        self._candidate_tools: list[dict[str, Any]] = []
        self._cfg: LumberroomConfig | None = None
        self._inert_reason: str | None = None
        self._availability_error = ""

        self._initialized = False
        self._session_id = ""
        self._hermes_home = ""
        self._cwd: str | None = None
        self._ident: gate.SessionIdentity | None = None
        self._agent_context = "primary"
        self._session_allowed = False

        self._bridge: Bridge | None = None
        self._auth: AuthHandle | None = None
        self._live_tools: list[dict[str, Any]] | None = None
        self._instructions: str | None = None
        self._builtin_off = False

        self._told_login_this_session = False

        self._digest_armed = False
        self._injected_ids = recall.InjectedIds()
        self._breaker = recall.Breaker(clock=clock)
        self._recall_status: RecallStatus | None = None

        self._current_author_id: str | None = None
        self._turns_since_nudge = 0
        self._nudge_due = False

    @property
    def name(self) -> str:
        return "lumberroom"

    # -- availability ---------------------------------------------------------

    def is_available(self) -> bool:
        try:
            cfg = config.load()
        except config.ConfigError as e:
            self._availability_error = str(e)
            return False
        if cfg.auth == "token":
            try:
                present = token_present(cfg)
            except Exception:
                present = False
            if not present:
                self._availability_error = f"{config.TOKEN_ENV} is not set"
                return False
        self._availability_error = ""
        return True

    def unavailable_reason(self) -> str:
        return self._availability_error

    # -- candidate set ----------------------------------------------------------

    def _ensure_candidate_tools(self) -> None:
        if self._candidate_computed:
            return
        self._candidate_computed = True
        try:
            cfg = config.load()
        except config.ConfigError as e:
            self._inert_reason = str(e)
            self._candidate_tools = []
            return
        self._cfg = cfg
        hermes_home = self._resolve_hermes_home()
        listing = schemas.read_cache(hermes_home) if hermes_home else None
        tools = listing["tools"] if listing else schemas.load_snapshot()["tools"]
        # review_queue and review_decide reach the model only through dreaming_review on a hosted
        # engine (owner ruling, 25 September 2026), never through the tools allowlist: an owner who
        # lists them there without the setting still gets neither.
        candidate = [t for t in schemas.select(cfg.tools, tools) if t["name"] not in config.REVIEW_TOOLS]
        if cfg.dreaming_review and cfg.is_hosted:
            candidate += schemas.select(config.REVIEW_TOOLS, tools)
        self._candidate_tools = candidate

    @staticmethod
    def _resolve_hermes_home() -> str | None:
        try:
            from hermes_constants import get_hermes_home

            return str(get_hermes_home())
        except Exception:
            return None

    def _read_builtin_off(self) -> bool:
        try:
            from hermes_cli.config import load_config

            memory_on, user_on = config.builtin_flags(load_config())
            return not memory_on and not user_on
        except Exception:
            return False

    # -- lifecycle --------------------------------------------------------------

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._ensure_candidate_tools()
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home") or self._resolve_hermes_home() or ""
        self._cwd = kwargs.get("cwd")
        self._initialized = True
        if self._inert_reason:
            return

        ident = gate.identity_from_kwargs(kwargs)
        self._ident = ident
        self._agent_context = ident.agent_context
        self._session_allowed = gate.session_allowed(ident, self._cfg)
        self._builtin_off = self._read_builtin_off()
        if not self._session_allowed:
            return

        try:
            auth = self._auth_factory(self._cfg, self._hermes_home)
        except Exception as e:
            logger.warning("lumberroom auth setup failed: %s", e)
            self._inert_reason = f"auth setup failed: {e}"
            return
        self._auth = auth

        bridge = self._bridge_factory(self._cfg, auth, session_id)
        self._bridge = bridge
        bridge.start()
        result, listing = bridge.list_tools(timeout=_INIT_TIMEOUT_S)
        if result.ok and listing is not None:
            self._live_tools = list(listing.tools)
            self._instructions = listing.instructions
            if self._hermes_home:
                try:
                    schemas.write_cache(self._hermes_home,
                                        {"instructions": listing.instructions, "tools": list(listing.tools)})
                except OSError as e:
                    logger.warning("lumberroom could not write the tool cache: %s", e)
        # Any other answer leaves live_tools unset for the life of this provider, and nothing
        # fetches the listing again. Schemas fall back to the frozen candidate set, and the engine
        # refuses a call the grant lacks.
        self._digest_armed = True

    def shutdown(self) -> None:
        if self._bridge is not None:
            self._bridge.close(timeout=2.0)

    # -- schemas and prompt -------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        self._ensure_candidate_tools()
        if self._inert_reason:
            return []
        if not self._initialized:
            return [schemas.to_hermes_schema(t) for t in self._candidate_tools]
        if not self._session_allowed:
            return []
        if self._live_tools is None:
            # No live listing to prove the server offers the review tools, so a degraded init
            # drops them; the ruling puts them behind the server's own tools/list.
            return [schemas.to_hermes_schema(t) for t in self._candidate_tools
                    if t["name"] not in config.REVIEW_TOOLS]
        live_names = {t["name"] for t in self._live_tools}
        return [schemas.to_hermes_schema(t) for t in self._candidate_tools if t["name"] in live_names]

    def _review_tool_offered(self, tool_name: str) -> bool:
        if tool_name not in config.REVIEW_TOOLS:
            return True
        return self._live_tools is not None and any(t["name"] == tool_name for t in self._live_tools)

    def system_prompt_block(self) -> str:
        if self._inert_reason or not self._session_allowed:
            return ""
        instructions = self._instructions or schemas.load_snapshot().get("instructions") or ""
        lines = [instructions] if instructions else []
        lines.append("Lumberroom is the durable memory here. Record durable facts with memory_write.")
        if self._builtin_off:
            lines.append("The built-in MEMORY.md and USER.md are off.")
        return "\n".join(lines)

    # -- turn tracking and gate ---------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        self._current_author_id = kwargs.get("author_id")

    def _turn_reaches_engine(self) -> bool:
        if not self._initialized or self._inert_reason or self._ident is None:
            return False
        return gate.turn_allowed(self._ident, self._current_author_id, self._cfg)

    # -- recall -------------------------------------------------------------------

    def _project(self) -> str | None:
        if self._cfg is None or self._cfg.project == "none":
            return None
        if self._cfg.project != "auto":
            return self._cfg.project
        if not self._cwd:
            return None
        path = Path(self._cwd)
        for candidate in (path, *path.parents):
            if (candidate / ".git").exists():
                return str(candidate)
        return None

    @staticmethod
    def _classify(result) -> str:
        if result.kind == "login_required":
            return "login"
        if result.kind in ("unreachable", "timeout"):
            return "outage"
        if result.kind == "ok":
            return "ok"
        return "tool_error"

    def _prefetch_login_line(self) -> str:
        # Said once per session, and never latched: the next turn asks the auth handle again, so a
        # login in another terminal takes effect without a restart.
        self._recall_status = None
        if self._told_login_this_session:
            return ""
        self._told_login_this_session = True
        return recall.LOGIN_LINE

    def _prefetch_outage_line(self) -> str:
        started = self._breaker.record_failure()
        self._recall_status = None
        return recall.UNREACHABLE_LINE if started else ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._ensure_candidate_tools()
        if self._inert_reason or self._cfg is None or not self._cfg.recall:
            return ""
        if not self._turn_reaches_engine():
            return ""
        if not self._breaker.allow():
            return ""

        project = self._project()
        calls: list[tuple[str, dict[str, Any]]] = []
        send_digest = self._cfg.digest and self._digest_armed
        if send_digest:
            calls.append(("context_bootstrap", {"project": project} if project else {}))
        search_args: dict[str, Any] = {"query": recall.clip_query(query), "limit": self._cfg.recall_limit}
        if project:
            search_args["project"] = project
        calls.append(("memory_search", search_args))

        results = self._bridge.call_many(calls, invocation="hook", timeout=self._cfg.prefetch_timeout_s)

        idx = 0
        digest_text = ""
        if send_digest:
            outcome = self._classify(results[idx])
            idx += 1
            if outcome == "login":
                return self._prefetch_login_line()
            if outcome == "outage":
                return self._prefetch_outage_line()
            if outcome == "ok":
                r = results[idx - 1]
                text = (r.structured or {}).get("text", "")
                digest_text = recall.format_digest(text, self._cfg.digest_max_chars)
                # Every return below carries digest_text, so this turn delivers it.
                self._digest_armed = False
            # a tool error leaves the digest unset and armed, to retry on the next turn

        search_result = results[idx]
        outcome = self._classify(search_result)
        hits_text = ""
        count = 0
        if outcome == "login":
            return recall.compose([digest_text, self._prefetch_login_line()])
        if outcome == "outage":
            return recall.compose([digest_text, self._prefetch_outage_line()])
        if outcome == "ok":
            hits = (search_result.structured or {}).get("hits", [])
            hits_text, ids = recall.format_hits(hits, self._injected_ids, self._cfg.recall_max_chars)
            self._injected_ids.add(ids)
            count = len(ids)
            self._breaker.record_success()
        # a tool error leaves the hits block empty for this turn only

        self._recall_status = RecallStatus("lumberroom", count) if count else None

        nudge = ""
        if self._nudge_due and self._agent_context == "primary":
            nudge = recall.NUDGE_LINE
            self._nudge_due = False

        return recall.compose([digest_text, hits_text, nudge])

    def recall_status(self) -> RecallStatus | None:
        return self._recall_status

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: list[dict[str, Any]] | None = None,
                  turn_author: dict[str, Any] | None = None) -> None:
        # No write buffer: lumberroom is the sole durable store and the model writes it directly.
        if self._inert_reason or not self._session_allowed or self._agent_context != "primary":
            return
        if self._cfg is None or self._cfg.review_interval <= 0:
            return
        # A refused turn in a shared chat must not ripen the nudge: it would reach the owner's next
        # turn and prompt writes of another member's claims. turn_author names this turn's writer,
        # and _current_author_id may already belong to the next turn on Hermes's sync worker.
        author_id = (turn_author or {}).get("id") or None
        if self._ident is None or not gate.turn_allowed(self._ident, author_id, self._cfg):
            return
        self._turns_since_nudge += 1
        if self._turns_since_nudge >= self._cfg.review_interval:
            self._nudge_due = True
            self._turns_since_nudge = 0

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False,
                          rewound: bool = False, **kwargs: Any) -> None:
        self._session_id = new_session_id
        if self._bridge is not None:
            self._bridge.set_session_id(new_session_id)
        self._injected_ids.clear()
        self._told_login_this_session = False
        # The summary the compressor writes may drop an earlier digest, so it goes out again.
        if reset or kwargs.get("reason") == "compression":
            self._digest_armed = True

    # -- tool calls -----------------------------------------------------------

    def _tool_result_json(self, tool_name: str, result) -> str:
        if result.kind == "ok":
            return json.dumps(result.structured if result.structured is not None else {})
        if result.kind == "unreachable":
            host = urllib.parse.urlsplit(self._cfg.origin).netloc
            return json.dumps({"error": f"lumberroom unreachable at {host}: {result.error}. "
                                        f"{_unreachable_tail(tool_name)}"})
        if result.kind == "timeout":
            return json.dumps({"error": f"lumberroom did not answer within {self._cfg.tool_timeout_s:g}s. "
                                        "The call may have taken effect; search before retrying."})
        if result.kind == "unauthorized":
            return json.dumps({"error": f"lumberroom refused {config.TOKEN_ENV} (401). "
                                        "Check the token and its grant."})
        if result.kind == "login_required":
            return json.dumps({"error": _LOGIN_REQUIRED_ERROR})
        return json.dumps({"error": result.error or result.text})

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        self._ensure_candidate_tools()
        # The frozen candidate set is what get_tool_schemas exposed. An inert provider has none,
        # so it falls back to every name it could expose and reports its own reason instead.
        exposed = {t["name"] for t in self._candidate_tools}
        if self._inert_reason:
            exposed |= _known_tool_names()
        if tool_name not in exposed or not self._review_tool_offered(tool_name):
            return json.dumps({"error": f"lumberroom does not expose the tool {tool_name!r}"})
        if self._inert_reason:
            return json.dumps({"error": self._inert_reason})
        if not self._turn_reaches_engine():
            return json.dumps({"error": gate.REFUSAL})
        if self._bridge is None:
            return json.dumps({"error": gate.REFUSAL})
        result = self._bridge.call(tool_name, dict(args), invocation="model", timeout=self._cfg.tool_timeout_s)
        return self._tool_result_json(tool_name, result)

    # -- setup, delegated to wizard ------------------------------------------------

    def get_config_schema(self) -> list[dict[str, Any]]:
        return wizard.get_config_schema()

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        wizard.save_values(values, hermes_home)

    def post_setup(self, hermes_home: str, config_dict: dict[str, Any]) -> None:
        wizard.post_setup(hermes_home, config_dict)

    def get_status_config(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        try:
            cfg = config.parse(provider_config)
        except config.ConfigError as e:
            return {"error": str(e)}
        credential_present = True
        if cfg.auth == "token":
            try:
                credential_present = token_present(cfg)
            except Exception:
                credential_present = False
        return {"base_url": cfg.base_url, "auth": cfg.auth, "credential_present": credential_present,
                "hint": "Run `hermes lumberroom status` for a live check."}

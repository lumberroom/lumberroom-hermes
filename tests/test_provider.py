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

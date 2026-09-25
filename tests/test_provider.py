import json
import threading

import pytest

from lumberroom_hermes import config
from lumberroom_hermes.bridge import CallResult, ToolsListing
from lumberroom_hermes.provider import LumberroomProvider
from lumberroom_hermes.recall import LOGIN_LINE, NUDGE_LINE, UNREACHABLE_LINE
from lumberroom_hermes.schemas import load_snapshot, write_cache

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


def test_oauth_without_a_token_says_log_in_once_per_session(config_yaml, hermes_home):
    config_yaml(auth="oauth")
    p, _ = make(FakeBridge(fail="login_required"))
    init(p, hermes_home)
    assert p.prefetch("q") == LOGIN_LINE
    assert p.prefetch("q") == ""
    assert "login" in json.loads(p.handle_tool_call("memory_search", {"query": "q"}))["error"]


def test_login_required_at_initialize_does_not_stop_the_first_prefetch_reaching_the_engine(config_yaml,
                                                                                          hermes_home):
    config_yaml(auth="oauth")
    bridge = FakeBridge(fail="login_required")
    p, _ = make(bridge)
    init(p, hermes_home)
    bridge.fail = None      # `hermes lumberroom login` ran in another terminal
    assert "Likes terse plans." in p.prefetch("q")


def test_a_prefetch_after_a_login_required_turn_asks_the_engine_again(config_yaml, hermes_home):
    config_yaml(auth="oauth")
    bridge = FakeBridge(fail="login_required")
    p, _ = make(bridge)
    init(p, hermes_home)
    assert p.prefetch("q") == LOGIN_LINE
    bridge.fail = None
    assert "Likes terse plans." in p.prefetch("q")


def test_a_tool_call_after_a_login_required_answer_asks_the_engine_again(config_yaml, hermes_home):
    config_yaml(auth="oauth")
    bridge = FakeBridge(fail="login_required")
    p, _ = make(bridge)
    init(p, hermes_home)
    write = {"content": "c", "namespace": "user:me"}
    assert "login" in json.loads(p.handle_tool_call("memory_write", write))["error"]
    bridge.fail = None
    assert json.loads(p.handle_tool_call("memory_write", write))["id"] == "w-1"


def test_a_search_timeout_still_delivers_the_digest_beside_the_outage_line(config_yaml, hermes_home):
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    bridge.answers["memory_search"] = CallResult("timeout", None, "", "no answer within 3.0s")
    first = p.prefetch("q")
    assert "- digest line" in first and UNREACHABLE_LINE in first
    bridge.answers["memory_search"] = OK({"hits": []})
    assert "digest line" not in p.prefetch("q")
    assert [t for t, *_ in bridge.calls].count("context_bootstrap") == 1


def test_a_search_login_required_still_delivers_the_digest_beside_the_login_line(config_yaml, hermes_home):
    config_yaml(auth="oauth")
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    bridge.answers["memory_search"] = CallResult("login_required", None, "", "LoginRequired")
    out = p.prefetch("q")
    assert "- digest line" in out and LOGIN_LINE in out


def test_a_tool_the_owner_allowlisted_from_the_live_cache_is_routed_to_the_engine(config_yaml, hermes_home):
    extra = {"name": "registry_set", "description": "set", "inputSchema": {"type": "object"}}
    tools = [t for t in load_snapshot()["tools"]] + [extra]
    write_cache(str(hermes_home), {"instructions": "I", "tools": tools})
    config_yaml(tools=["memory_search", "registry_set"])
    bridge = FakeBridge(tools=tools)
    bridge.answers["registry_set"] = OK({"key": "k"})
    p, _ = make(bridge)
    init(p, hermes_home)
    assert "registry_set" in {s["name"] for s in p.get_tool_schemas()}
    assert json.loads(p.handle_tool_call("registry_set", {"key": "k"})) == {"key": "k"}


def test_a_snapshot_tool_outside_the_allowlist_is_refused_before_the_engine(config_yaml, hermes_home):
    config_yaml(tools=["memory_search"])
    bridge = FakeBridge()
    p, _ = make(bridge)
    init(p, hermes_home)
    err = json.loads(p.handle_tool_call("memory_write", {"content": "c", "namespace": "user:me"}))["error"]
    assert "does not expose" in err and bridge.calls == []


def test_a_bad_config_reports_its_own_reason_for_a_real_tool_name(monkeypatch, hermes_home):
    monkeypatch.setattr("hermes_cli.config.load_config",
                        lambda: {"memory": {"lumberroom": {"base_url": "http://x.test", "auth": "nonsense"}}})
    p, _ = make(FakeBridge())
    err = json.loads(p.handle_tool_call("memory_search", {"query": "q"}))["error"]
    assert "does not expose" not in err and err


def test_refused_turns_in_a_shared_chat_do_not_count_toward_the_nudge(config_yaml, hermes_home):
    config_yaml(owner_user_ids=["telegram:42"], review_interval=2)
    p, _ = make(FakeBridge())
    init(p, hermes_home, platform="telegram", user_id="7", chat_type="group")
    p.sync_turn("u", "a", turn_author={"id": "7", "name": "guest", "is_bot": False})
    p.sync_turn("u", "a")
    p.on_turn_start(3, "q", author_id="42")
    assert NUDGE_LINE not in p.prefetch("q")


def test_owner_turns_in_a_shared_chat_count_toward_the_nudge(config_yaml, hermes_home):
    config_yaml(owner_user_ids=["telegram:42"], review_interval=2)
    p, _ = make(FakeBridge())
    init(p, hermes_home, platform="telegram", user_id="7", chat_type="group")
    owner = {"id": "42", "name": "owner", "is_bot": False}
    p.sync_turn("u", "a", turn_author=owner)
    p.sync_turn("u", "a", turn_author=owner)
    p.on_turn_start(3, "q", author_id="42")
    assert NUDGE_LINE in p.prefetch("q")


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


def test_review_tools_are_exposed_on_hosted_with_the_setting_on_and_the_live_list_offering_them(
        config_yaml, hermes_home):
    config_yaml(base_url="https://mcp.lumberroom.cloud", dreaming_review=True)
    p, _ = make(FakeBridge())
    init(p, hermes_home)
    names = {s["name"] for s in p.get_tool_schemas()}
    assert {"review_queue", "review_decide"} <= names


def test_review_tools_stay_hidden_when_the_live_list_lacks_them(config_yaml, hermes_home):
    config_yaml(base_url="https://mcp.lumberroom.cloud", dreaming_review=True)
    tools = [t for t in load_snapshot()["tools"] if t["name"] not in ("review_queue", "review_decide")]
    p, _ = make(FakeBridge(tools=tools))
    before = {s["name"] for s in p.get_tool_schemas()}
    assert {"review_queue", "review_decide"} <= before
    p.initialize("s-1", hermes_home=str(hermes_home), platform="cli")
    after = {s["name"] for s in p.get_tool_schemas()}
    assert not {"review_queue", "review_decide"} & after
    assert after <= before


def test_review_tools_stay_hidden_with_the_setting_off(config_yaml, hermes_home):
    config_yaml(base_url="https://mcp.lumberroom.cloud", dreaming_review=False)
    p, _ = make(FakeBridge())
    init(p, hermes_home)
    names = {s["name"] for s in p.get_tool_schemas()}
    assert not {"review_queue", "review_decide"} & names


def test_review_tools_stay_hidden_on_a_self_hosted_base_url_with_the_setting_on(config_yaml, hermes_home):
    config_yaml(base_url="http://fake.lumberroom.test", dreaming_review=True)
    p, _ = make(FakeBridge())
    init(p, hermes_home)
    names = {s["name"] for s in p.get_tool_schemas()}
    assert not {"review_queue", "review_decide"} & names


def test_review_tools_stay_hidden_when_listed_in_tools_without_the_setting(config_yaml, hermes_home):
    config_yaml(base_url="https://mcp.lumberroom.cloud", dreaming_review=False,
                tools=list(config.DEFAULT_TOOLS) + ["review_queue", "review_decide"])
    p, _ = make(FakeBridge())
    init(p, hermes_home)
    names = {s["name"] for s in p.get_tool_schemas()}
    assert not {"review_queue", "review_decide"} & names


def test_review_tools_pre_init_candidate_set_is_a_superset_of_post_init(config_yaml, hermes_home):
    config_yaml(base_url="https://mcp.lumberroom.cloud", dreaming_review=True)
    p, _ = make(FakeBridge())
    before = {s["name"] for s in p.get_tool_schemas()}
    p.initialize("s-1", hermes_home=str(hermes_home), platform="cli")
    after = {s["name"] for s in p.get_tool_schemas()}
    assert after <= before


def test_review_queue_and_review_decide_route_to_the_engine_when_exposed(config_yaml, hermes_home):
    config_yaml(base_url="https://mcp.lumberroom.cloud", dreaming_review=True)
    bridge = FakeBridge()
    bridge.answers["review_queue"] = OK({"items": []})
    bridge.answers["review_decide"] = OK({"ok": True})
    p, _ = make(bridge)
    init(p, hermes_home)
    assert json.loads(p.handle_tool_call("review_queue", {}))["items"] == []
    assert json.loads(p.handle_tool_call("review_decide", {"id": "x", "decision": "approve"}))["ok"] is True
    assert [c[0] for c in bridge.calls] == ["review_queue", "review_decide"]


def test_hermes_routes_every_exposed_tool(config_yaml, hermes_home):
    from agent.memory_manager import MemoryManager
    p, _ = make(FakeBridge())
    mm = MemoryManager()
    mm.add_provider(p)
    mm.initialize_all(session_id="s-1", platform="cli", hermes_home=str(hermes_home))
    exposed = {s["name"] for s in mm.get_all_tool_schemas()}
    assert exposed and exposed <= mm.get_all_tool_names()
    mm.shutdown_all()


def test_review_tools_stay_hidden_and_refused_when_no_live_list_came_back(config_yaml, hermes_home):
    config_yaml(base_url="https://mcp.lumberroom.cloud", dreaming_review=True)
    bridge = FakeBridge(fail="timeout")
    p, _ = make(bridge)
    init(p, hermes_home)
    names = {s["name"] for s in p.get_tool_schemas()}
    assert "memory_search" in names
    assert not {"review_queue", "review_decide"} & names
    out = json.loads(p.handle_tool_call("review_queue", {}))
    assert "does not expose" in out["error"]
    assert not any(c[0] == "review_queue" for c in bridge.calls)

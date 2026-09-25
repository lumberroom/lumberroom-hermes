import contextlib
import threading
import time

import httpx2
import pytest

from lumberroom_hermes.auth import LoginRequired
from lumberroom_hermes.bridge import Bridge
from lumberroom_hermes.tokens import FenceTimeout


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


class Bearer(httpx2.Auth):
    def __init__(self, token):
        self.token = token

    def auth_flow(self, request):
        request.headers["authorization"] = f"Bearer {self.token}"
        yield request


class RetryingAuth(httpx2.Auth):
    """Answers a 401 with a second attempt under a new token, the way an OAuth refresh does."""

    def auth_flow(self, request):
        request.headers["authorization"] = "Bearer stale"
        response = yield request
        if response.status_code == 401:
            request.headers["authorization"] = "Bearer fresh"
            yield request


class OAuthLike:
    """An auth handle whose httpx auth an owner can swap, as a peer refresh does."""

    mode = "oauth"

    def __init__(self, inner):
        self.inner = inner

    def headers(self):
        return {}

    def httpx_auth(self):
        return self.inner

    @contextlib.asynccontextmanager
    async def refresh_guard(self):
        yield


def only_fresh_tokens(fake_engine):
    """A client factory whose engine answers 401 to any bearer other than "fresh"."""

    async def app(scope, receive, send):
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if scope["type"] == "http" and headers.get("authorization") != "Bearer fresh":
            await fake_engine._send(send, 401, {"error": "unauthorized"})
            return
        await fake_engine.app(scope, receive, send)

    def factory(**kw):
        kw.pop("transport", None)
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kw)

    return factory


@pytest.fixture
def bridge(fake_engine, cfg):
    b = Bridge(cfg, StaticAuth(), session_id="s-1", client_factory=fake_engine.client_factory)
    b.start()
    yield b
    b.close()


@contextlib.contextmanager
def running(cfg, auth, factory, session_id="s"):
    b = Bridge(cfg, auth, session_id=session_id, client_factory=factory)
    b.start()
    try:
        yield b
    finally:
        b.close()


def test_hook_calls_carry_the_hook_header_and_model_calls_carry_none(bridge, fake_engine):
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    hook, model = fake_engine.tool_calls()
    assert hook.headers["x-memory-invocation"] == "hook"
    assert "x-memory-invocation" not in model.headers
    assert hook.headers["x-session-id"] == model.headers["x-session-id"] == "s-1"
    assert hook.headers["authorization"] == "Bearer t-1"


def test_every_request_names_the_plugin_in_its_user_agent(bridge, fake_engine):
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    assert fake_engine.tool_calls()[0].headers["user-agent"].startswith("lumberroom-hermes/")


def test_a_session_switch_changes_the_header_on_both_sessions(bridge, fake_engine):
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    bridge.set_session_id("s-2")
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    assert [c.headers["x-session-id"] for c in fake_engine.tool_calls()[2:]] == ["s-2", "s-2"]


def test_a_session_opened_after_a_switch_carries_the_new_id(bridge, fake_engine):
    bridge.set_session_id("s-9")
    bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    assert fake_engine.tool_calls()[0].headers["x-session-id"] == "s-9"


def test_structured_content_comes_back_unchanged(bridge, fake_engine):
    payload = {"id": "a", "namespace": "user:me", "sensitivity": "open", "deduplicated": False,
               "possible_conflicts": [{"id": "b", "namespace": "user:me", "content": "old", "similarity": 0.93}]}
    fake_engine.answer("memory_write", structured=payload)
    r = bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    assert r.kind == "ok" and r.structured == payload


def test_the_arguments_reach_the_engine_verbatim(bridge, fake_engine):
    args = {"query": "q", "limit": 4, "project": "lumberroom"}
    bridge.call("memory_search", args, invocation="hook", timeout=5)
    params = fake_engine.tool_calls()[0].body["params"]
    assert (params["name"], params["arguments"]) == ("memory_search", args)


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


def test_a_handshake_that_outlives_the_timeout_is_unreachable(bridge, fake_engine):
    # The tool call never left, so the provider may say nothing was stored.
    fake_engine.delay_s = 5
    r = bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=0.5)
    assert r.kind == "unreachable"
    assert fake_engine.tool_calls() == []


def test_a_call_after_a_timeout_opens_a_fresh_session_and_succeeds(bridge, fake_engine):
    bridge.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
    fake_engine.delay_s = 5
    assert bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=0.3).kind == "timeout"
    fake_engine.delay_s = 0
    assert bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "ok"


def test_a_timeout_retires_the_session_so_the_next_call_handshakes_again(bridge, fake_engine):
    def handshakes():
        return sum(1 for r in fake_engine.requests if isinstance(r.body, dict) and r.body.get("method") == "initialize")

    bridge.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
    fake_engine.delay_s = 5
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=0.3)
    fake_engine.delay_s = 0
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    assert handshakes() == 2


def test_call_many_shares_one_deadline(bridge, fake_engine):
    bridge.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
    fake_engine.delay_s = 0.3
    started = time.monotonic()
    results = bridge.call_many([("context_bootstrap", {}), ("memory_search", {"query": "q"})],
                               invocation="hook", timeout=2)
    assert [r.kind for r in results] == ["ok", "ok"]
    assert time.monotonic() - started < 0.55


def test_call_many_returns_results_in_input_order(bridge, fake_engine):
    fake_engine.answer("context_bootstrap", structured={"text": "digest"})
    fake_engine.answer("memory_search", structured={"hits": []})
    results = bridge.call_many([("memory_search", {"query": "q"}), ("context_bootstrap", {})],
                               invocation="hook", timeout=5)
    assert [r.structured for r in results] == [{"hits": []}, {"text": "digest"}]


def test_call_many_past_its_deadline_returns_every_result_within_the_bound(bridge, fake_engine):
    bridge.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
    fake_engine.delay_s = 5
    started = time.monotonic()
    results = bridge.call_many([("context_bootstrap", {}), ("memory_search", {"query": "q"})],
                               invocation="hook", timeout=0.5)
    assert [r.kind for r in results] == ["timeout", "timeout"]
    assert time.monotonic() - started < 0.8


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


def test_a_connection_dropped_after_the_request_left_is_a_timeout(cfg, fake_engine):
    # A write may have landed, so the provider must not claim nothing was stored.
    dropped = {"on": False}

    async def app(scope, receive, send):
        if dropped["on"]:
            raise httpx2.ReadError("connection reset")
        await fake_engine.app(scope, receive, send)

    def factory(**kw):
        kw.pop("transport", None)
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kw)

    with running(cfg, StaticAuth(), factory) as b:
        b.call("memory_write", {"content": "warm", "namespace": "user:me"}, invocation="model", timeout=5)
        dropped["on"] = True
        r = b.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    assert r.kind == "timeout" and "connection reset" in r.error


def test_a_401_is_unauthorized(bridge, fake_engine):
    fake_engine.status_override = 401
    assert bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "unauthorized"


def test_a_401_on_an_open_session_is_unauthorized(bridge, fake_engine):
    bridge.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
    fake_engine.status_override = 401
    r = bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="hook", timeout=5)
    assert r.kind == "unauthorized"


def test_a_401_the_auth_flow_recovers_from_is_not_unauthorized(cfg, fake_engine):
    with running(cfg, OAuthLike(RetryingAuth()), only_fresh_tokens(fake_engine)) as b:
        r = b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    assert r.kind == "ok"


def test_the_bridge_asks_the_auth_handle_for_its_http_auth_on_every_request(cfg, fake_engine):
    handle = OAuthLike(Bearer("stale"))
    with running(cfg, handle, only_fresh_tokens(fake_engine)) as b:
        assert b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "unauthorized"
        handle.inner = Bearer("fresh")
        assert b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "ok"


def test_an_http_auth_swapped_under_an_open_session_signs_the_next_request(cfg, fake_engine):
    handle = OAuthLike(Bearer("first"))
    with running(cfg, handle, fake_engine.client_factory) as b:
        b.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
        handle.inner = Bearer("second")
        b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    assert [c.headers["authorization"] for c in fake_engine.tool_calls()] == ["Bearer first", "Bearer second"]


def test_a_503_is_unreachable(bridge, fake_engine):
    fake_engine.status_override = 503
    r = bridge.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    assert r.kind == "unreachable" and "503" in r.error


def test_login_required_from_the_auth_guard_surfaces_as_its_own_kind(fake_engine, cfg):
    b = Bridge(cfg, StaticAuth(raise_login=True), session_id="s", client_factory=fake_engine.client_factory)
    b.start()
    try:
        assert b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "login_required"
    finally:
        b.close()


def test_login_required_raised_inside_the_http_auth_flow_surfaces_as_its_own_kind(cfg, fake_engine):
    class NeedsBrowser(httpx2.Auth):
        def auth_flow(self, request):
            raise LoginRequired("no browser here")
            yield request

    with running(cfg, OAuthLike(NeedsBrowser()), fake_engine.client_factory) as b:
        assert b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "login_required"


def test_a_fence_timeout_is_unreachable(cfg, fake_engine):
    class Stuck(OAuthLike):
        @contextlib.asynccontextmanager
        async def refresh_guard(self):
            raise FenceTimeout("a peer held oauth.lock")
            yield

    with running(cfg, Stuck(None), fake_engine.client_factory) as b:
        r = b.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
    assert r.kind == "unreachable"
    assert fake_engine.requests == []


def test_a_session_that_lost_its_connection_reconnects_on_the_next_call(bridge, fake_engine):
    fake_engine.status_override = 503
    bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=2)
    fake_engine.status_override = None
    assert bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "ok"


def test_an_open_session_that_hits_a_503_reconnects_on_the_next_call(bridge, fake_engine):
    bridge.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
    fake_engine.status_override = 503
    assert bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=2).kind == "unreachable"
    fake_engine.status_override = None
    assert bridge.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind == "ok"


def test_list_tools_returns_the_engine_instructions_and_tools(bridge):
    result, listing = bridge.list_tools(timeout=5)
    assert result.kind == "ok"
    assert "memory_write" in {t["name"] for t in listing.tools}
    assert listing.instructions and "memory_write" in listing.instructions


def test_list_tools_keeps_the_camel_case_schema_key(bridge):
    _, listing = bridge.list_tools(timeout=5)
    assert all("inputSchema" in t for t in listing.tools)


def test_list_tools_reflects_the_grant(bridge, fake_engine):
    fake_engine.hide_tools = {"memory_forget"}
    _, listing = bridge.list_tools(timeout=5)
    assert "memory_forget" not in {t["name"] for t in listing.tools}


def test_list_tools_goes_out_on_the_hook_session(bridge, fake_engine):
    bridge.list_tools(timeout=5)
    listed = [r for r in fake_engine.requests if isinstance(r.body, dict) and r.body.get("method") == "tools/list"]
    assert listed and all(r.headers["x-memory-invocation"] == "hook" for r in listed)


def test_list_tools_against_a_dead_engine_returns_no_listing(bridge, fake_engine):
    fake_engine.status_override = 503
    result, listing = bridge.list_tools(timeout=2)
    assert result.kind == "unreachable" and listing is None


def test_http_json_goes_to_the_origin_without_the_invocation_header(bridge, fake_engine):
    fake_engine.admin[("POST", "/admin/ingest/runs")] = lambda body: (200, {"run_id": "r-1"})
    status, body = bridge.http_json("POST", "/admin/ingest/runs", {"extractor": "x"}, timeout=5)
    assert (status, body) == (200, {"run_id": "r-1"})
    assert "x-memory-invocation" not in fake_engine.requests[-1].headers


def test_http_json_sends_the_body_and_the_credential(bridge, fake_engine):
    fake_engine.admin[("POST", "/admin/ingest/runs")] = lambda body: (200, {"echo": body})
    status, body = bridge.http_json("POST", "/admin/ingest/runs", {"extractor": "x"}, timeout=5)
    assert body == {"echo": {"extractor": "x"}}
    assert fake_engine.requests[-1].headers["authorization"] == "Bearer t-1"
    assert fake_engine.requests[-1].headers["x-session-id"] == "s-1"


def test_http_json_hands_back_an_error_status_to_the_caller(bridge, fake_engine):
    fake_engine.admin[("POST", "/admin/ingest/runs")] = lambda body: (403, {"error": "forbidden"})
    assert bridge.http_json("POST", "/admin/ingest/runs", {}, timeout=5) == (403, {"error": "forbidden"})


def test_a_call_after_close_is_unreachable_and_does_not_raise(fake_engine, cfg):
    b = Bridge(cfg, StaticAuth(), session_id="s", client_factory=fake_engine.client_factory)
    b.start()
    b.close()
    assert b.call("memory_search", {"query": "q"}, invocation="hook", timeout=1).kind == "unreachable"


def test_close_returns_within_two_seconds_with_a_call_in_flight(fake_engine, cfg):
    b = Bridge(cfg, StaticAuth(), session_id="s", client_factory=fake_engine.client_factory)
    b.start()
    fake_engine.delay_s = 30
    threading.Thread(target=lambda: b.call("memory_search", {"query": "q"}, invocation="hook", timeout=30), daemon=True).start()
    time.sleep(0.2)
    started = time.monotonic()
    b.close(timeout=2.0)
    assert time.monotonic() - started < 2.3


def test_a_call_in_flight_at_close_returns_a_result(fake_engine, cfg):
    b = Bridge(cfg, StaticAuth(), session_id="s", client_factory=fake_engine.client_factory)
    b.start()
    b.call("memory_search", {"query": "warm"}, invocation="hook", timeout=5)
    fake_engine.delay_s = 30
    out = []
    t = threading.Thread(target=lambda: out.append(b.call("memory_search", {"query": "q"}, invocation="hook", timeout=30)))
    t.start()
    time.sleep(0.2)
    b.close(timeout=2.0)
    t.join(3)
    assert [r.kind for r in out] == ["timeout"]


def test_close_closes_every_http_client_the_bridge_opened(fake_engine, cfg):
    made = []

    def recording(**kw):
        client = fake_engine.client_factory(**kw)
        made.append(client)
        return client

    with running(cfg, StaticAuth(), recording) as b:
        b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
        b.call("memory_write", {"content": "c", "namespace": "user:me"}, invocation="model", timeout=5)
        fake_engine.admin[("GET", "/health")] = lambda body: (200, {})
        b.http_json("GET", "/health", None, timeout=5)
    assert len(made) == 3 and all(c.is_closed for c in made)


def test_the_loop_thread_is_gone_after_close(fake_engine, cfg):
    before = set(threading.enumerate())
    b = Bridge(cfg, StaticAuth(), session_id="s", client_factory=fake_engine.client_factory)
    b.start()
    b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    ours = [t for t in threading.enumerate() if t not in before and t.name == "lumberroom-bridge"]
    assert len(ours) == 1
    b.close()
    ours[0].join(1)
    assert not ours[0].is_alive()

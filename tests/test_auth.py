import asyncio
import dataclasses
import json
import socket
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from lumberroom_hermes import auth as auth_mod
from lumberroom_hermes.auth import (CLIENT_NAME, AuthConfigError, LoginFailed, LoginRequired, build_auth, login,
                                    logout, parse_callback, token_present)
from lumberroom_hermes.config import LumberroomConfig
from lumberroom_hermes.tokens import FenceTimeout, FileTokenStorage, token_paths

TOKEN = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="token")
OAUTH = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="oauth")
METADATA = {"issuer": "http://fake.lumberroom.test", "authorization_endpoint": "http://fake.lumberroom.test/oauth/authorize",
            "token_endpoint": "http://fake.lumberroom.test/oauth/token", "response_types_supported": ["code"]}


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


def test_a_blank_token_counts_as_missing(monkeypatch, hermes_home):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "  ")
    with pytest.raises(AuthConfigError, match="LUMBERROOM_HERMES_TOKEN"):
        build_auth(TOKEN, hermes_home=str(hermes_home))
    assert token_present(TOKEN) is False


def test_token_present_is_true_when_the_secret_is_set(monkeypatch):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    assert token_present(TOKEN) is True


def test_token_auth_hands_the_bridge_no_httpx_auth_and_an_open_guard(monkeypatch, hermes_home):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    handle = build_auth(TOKEN, hermes_home=str(hermes_home))

    async def go():
        async with handle.refresh_guard():
            return "ran"
    assert (handle.mode, handle.httpx_auth(), asyncio.run(go())) == ("token", None, "ran")


def test_oauth_is_present_without_a_token_file(hermes_home):
    assert token_present(OAUTH) is True


def test_oauth_reads_no_secret(monkeypatch, hermes_home):
    def refuse(name, default=None):
        raise AssertionError(f"read {name}")
    monkeypatch.setattr("agent.secret_scope.get_secret", refuse)
    assert build_auth(OAUTH, hermes_home=str(hermes_home)).mode == "oauth"


def test_the_two_context_fields_the_plugin_sets_exist_in_the_installed_sdk():
    from mcp.client.auth.oauth2 import OAuthContext
    names = {f.name for f in dataclasses.fields(OAuthContext)}
    assert {"token_expiry_time", "oauth_metadata"} <= names


def test_oauth_registers_as_a_public_loopback_client(hermes_home):
    cfg = dataclasses.replace(OAUTH, oauth_callback_port=50123)
    meta = build_auth(cfg, hermes_home=str(hermes_home)).httpx_auth().context.client_metadata
    assert meta.client_name == CLIENT_NAME == "Hermes Agent (lumberroom)"
    assert [str(u) for u in meta.redirect_uris] == ["http://127.0.0.1:50123/callback"]
    assert (list(meta.grant_types), list(meta.response_types), meta.token_endpoint_auth_method) == (
        ["authorization_code", "refresh_token"], ["code"], "none")


def test_oauth_points_the_sdk_at_the_mcp_url(hermes_home):
    assert build_auth(OAUTH, hermes_home=str(hermes_home)).httpx_auth().context.server_url == OAUTH.mcp_url


def test_oauth_seeds_expiry_and_metadata_from_disk(hermes_home):
    from mcp.shared.auth import OAuthToken
    path, _ = token_paths(str(hermes_home))
    path.parent.mkdir(parents=True)
    s = FileTokenStorage(path, OAUTH.mcp_url, clock=lambda: 1000.0)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=30, refresh_token="r")))
    s.save_metadata(METADATA)
    handle = build_auth(OAUTH, hermes_home=str(hermes_home))
    ctx = handle.httpx_auth().context
    assert ctx.token_expiry_time == 1030.0
    assert str(ctx.oauth_metadata.token_endpoint) == "http://fake.lumberroom.test/oauth/token"


def test_non_interactive_handlers_raise_login_required(hermes_home):
    handle = build_auth(OAUTH, hermes_home=str(hermes_home), interactive=False)
    with pytest.raises(LoginRequired):
        asyncio.run(handle.httpx_auth().context.redirect_handler("http://x/authorize"))


def test_the_non_interactive_callback_handler_raises_login_required(hermes_home):
    handle = build_auth(OAUTH, hermes_home=str(hermes_home), interactive=False)
    with pytest.raises(LoginRequired):
        asyncio.run(handle.httpx_auth().context.callback_handler())


def test_parse_callback_reads_code_and_state():
    assert parse_callback("http://127.0.0.1:47631/callback?code=c-1&state=s-1") == ("c-1", "s-1")


def test_parse_callback_takes_a_pasted_url_with_surrounding_whitespace():
    assert parse_callback("  http://127.0.0.1:47631/callback?code=c-1&state=s-1\n") == ("c-1", "s-1")


def test_parse_callback_without_state_returns_none_for_it():
    assert parse_callback("http://127.0.0.1:47631/callback?code=c-1") == ("c-1", None)


def test_parse_callback_refuses_an_error_redirect():
    with pytest.raises(LoginFailed, match="access_denied"):
        parse_callback("http://127.0.0.1:47631/callback?error=access_denied&state=s")


def test_parse_callback_refuses_a_url_without_a_code():
    with pytest.raises(LoginFailed, match="code"):
        parse_callback("http://127.0.0.1:47631/callback?state=s")


# The interactive handlers.

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def interactive(hermes_home, *, open_browser=False, read_pasted=None, port=None):
    lines = []
    cfg = dataclasses.replace(OAUTH, oauth_callback_port=port or free_port())
    handle = build_auth(cfg, hermes_home=str(hermes_home), interactive=True, open_browser=open_browser,
                        read_pasted=read_pasted, out=lines.append)
    return cfg, handle, lines


def _one_paste():
    pasted = iter(["http://127.0.0.1:1/callback?code=c&state=s"])
    return lambda: next(pasted)


def _sign_in_by_paste(handle, url):
    # Runs the callback too, so the listener and the paste reader shut down with the test.
    async def go():
        ctx = handle.httpx_auth().context
        await ctx.redirect_handler(url)
        return await ctx.callback_handler()
    return asyncio.run(go())


def test_the_redirect_handler_prints_the_url_alone_on_its_own_line(hermes_home, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    _, handle, lines = interactive(hermes_home, open_browser=False, read_pasted=_one_paste())
    _sign_in_by_paste(handle, "http://fake.lumberroom.test/oauth/authorize?state=s")
    assert lines == ["Open this URL to sign in:", "http://fake.lumberroom.test/oauth/authorize?state=s"]
    assert opened == []


def test_the_redirect_handler_opens_the_browser_when_allowed(hermes_home, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    _, handle, lines = interactive(hermes_home, open_browser=True, read_pasted=_one_paste())
    _sign_in_by_paste(handle, "http://x/authorize?state=s")
    assert opened == ["http://x/authorize?state=s"] and lines[-1] == "http://x/authorize?state=s"


def test_the_callback_handler_returns_a_pasted_redirect(hermes_home):
    pasted = iter(["", "http://127.0.0.1:1/callback?code=c-1&state=s-1&iss=http%3A%2F%2Ffake.lumberroom.test"])
    _, handle, _ = interactive(hermes_home, read_pasted=lambda: next(pasted))

    async def go():
        ctx = handle.httpx_auth().context
        await ctx.redirect_handler("http://x/authorize")
        return await ctx.callback_handler()
    result = asyncio.run(go())
    assert (result.code, result.state, result.iss) == ("c-1", "s-1", "http://fake.lumberroom.test")


def test_the_callback_handler_returns_the_loopback_redirect(hermes_home):
    never = threading.Event()
    cfg, handle, _ = interactive(hermes_home, read_pasted=lambda: never.wait(30) and "")
    url = f"http://127.0.0.1:{cfg.oauth_callback_port}/callback?code=c-2&state=s-2"
    bodies = []

    def browser():
        time.sleep(0.2)
        with urllib.request.urlopen(url, timeout=5) as r:
            bodies.append((r.status, r.read().decode()))

    async def go():
        ctx = handle.httpx_auth().context
        await ctx.redirect_handler("http://x/authorize")
        threading.Thread(target=browser, daemon=True).start()
        return await ctx.callback_handler()
    try:
        result = asyncio.run(go())
    finally:
        never.set()
    assert (result.code, result.state) == ("c-2", "s-2")
    assert bodies and bodies[0][0] == 200


def test_the_loopback_listener_closes_after_the_login(hermes_home):
    pasted = iter(["http://127.0.0.1:1/callback?code=c&state=s"])
    cfg, handle, _ = interactive(hermes_home, read_pasted=lambda: next(pasted))

    async def go():
        ctx = handle.httpx_auth().context
        await ctx.redirect_handler("http://x/authorize")
        await ctx.callback_handler()
    asyncio.run(go())
    with socket.socket() as s:
        s.bind(("127.0.0.1", cfg.oauth_callback_port))


def test_a_pasted_error_redirect_fails_the_login(hermes_home):
    _, handle, _ = interactive(hermes_home, read_pasted=lambda: "http://127.0.0.1:1/callback?error=access_denied")

    async def go():
        ctx = handle.httpx_auth().context
        await ctx.redirect_handler("http://x/authorize")
        await ctx.callback_handler()
    with pytest.raises(LoginFailed, match="access_denied"):
        asyncio.run(go())


def test_the_callback_handler_gives_up_after_its_timeout(hermes_home, monkeypatch):
    monkeypatch.setattr(auth_mod, "_CALLBACK_TIMEOUT_S", 0.3)
    never = threading.Event()
    _, handle, _ = interactive(hermes_home, read_pasted=lambda: never.wait(30) and "")

    async def go():
        ctx = handle.httpx_auth().context
        await ctx.redirect_handler("http://x/authorize")
        await ctx.callback_handler()
    try:
        with pytest.raises(LoginFailed, match="timed out"):
            asyncio.run(go())
    finally:
        never.set()


# refresh_guard.

class _NoFence:
    def __init__(self, log=None, on_enter=None):
        self.log, self.on_enter = log if log is not None else [], on_enter

    async def __aenter__(self):
        self.log.append("fence in")
        if self.on_enter:
            self.on_enter()
        return self

    async def __aexit__(self, *exc):
        self.log.append("fence out")
        return None


def _write_pair(hermes_home, seconds_left, access="a", refresh="r"):
    from mcp.shared.auth import OAuthToken
    path, _ = token_paths(str(hermes_home))
    path.parent.mkdir(parents=True, exist_ok=True)
    s = FileTokenStorage(path, OAUTH.mcp_url)
    asyncio.run(s.set_tokens(OAuthToken(access_token=access, token_type="Bearer", expires_in=seconds_left,
                                        refresh_token=refresh)))
    return s


def _oauth_with_expiry(hermes_home, seconds_left):
    _write_pair(hermes_home, seconds_left)
    return build_auth(OAUTH, hermes_home=str(hermes_home))


def _run_guard(handle, log=None):
    async def go():
        async with handle.refresh_guard():
            if log is not None:
                log.append("body")
    asyncio.run(go())


def test_refresh_guard_skips_the_fence_while_the_token_is_fresh(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    handle = _oauth_with_expiry(hermes_home, seconds_left=3000)
    _run_guard(handle)
    assert entered == []


def test_refresh_guard_takes_the_fence_inside_the_skew(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    handle = _oauth_with_expiry(hermes_home, seconds_left=10)
    _run_guard(handle)
    assert entered == [1]


def test_refresh_guard_takes_the_fence_when_the_file_records_no_expiry(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    handle = _oauth_with_expiry(hermes_home, seconds_left=None)
    _run_guard(handle)
    assert entered == [1]


def test_refresh_guard_raises_login_required_before_any_request_when_logged_out(hermes_home, monkeypatch):
    # Without this every hook call would run a 401, discovery and a logged traceback first.
    entered, log = [], []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    with pytest.raises(LoginRequired):
        _run_guard(build_auth(OAUTH, hermes_home=str(hermes_home)), log)
    assert (entered, log) == ([], [])


def test_refresh_guard_skips_the_fence_for_a_pair_without_a_refresh_token(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    _write_pair(hermes_home, 10, refresh=None)
    _run_guard(build_auth(OAUTH, hermes_home=str(hermes_home)))
    assert entered == []


def test_refresh_guard_holds_the_fence_through_the_request_when_still_due(hermes_home, monkeypatch):
    log = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: _NoFence(log))
    handle = _oauth_with_expiry(hermes_home, seconds_left=10)
    _run_guard(handle, log)
    assert log == ["fence in", "body", "fence out"]


def test_refresh_guard_adopts_a_pair_a_peer_refreshed_while_it_waited(hermes_home, monkeypatch):
    log = []
    handle = _oauth_with_expiry(hermes_home, seconds_left=10)

    def peer_wins():
        # The fence runs inside the guard's loop, and _write_pair starts a loop of its own.
        t = threading.Thread(target=_write_pair, args=(hermes_home, 3600), kwargs={"access": "peer", "refresh": "peer-r"})
        t.start()
        t.join()
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: _NoFence(log, on_enter=peer_wins))
    _run_guard(handle, log)
    ctx = handle.httpx_auth().context
    assert log == ["fence in", "fence out", "body"]
    assert (ctx.current_tokens.access_token, ctx.current_tokens.refresh_token) == ("peer", "peer-r")
    assert ctx.token_expiry_time > time.time() + 3000


def test_refresh_guard_reloads_when_a_peer_rewrote_the_file(hermes_home, monkeypatch):
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: _NoFence())
    handle = _oauth_with_expiry(hermes_home, seconds_left=3000)
    time.sleep(0.01)
    _write_pair(hermes_home, 3600, access="peer")
    _run_guard(handle)
    assert handle.httpx_auth().context.current_tokens.access_token == "peer"


def test_refresh_guard_forgets_the_pair_after_a_peer_logged_out(hermes_home, monkeypatch):
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: _NoFence())
    handle = _oauth_with_expiry(hermes_home, seconds_left=3000)
    logout(OAUTH, hermes_home=str(hermes_home))
    with pytest.raises(LoginRequired):
        _run_guard(handle)
    assert handle.httpx_auth().context.current_tokens is None


def test_refresh_guard_lets_fence_timeout_reach_the_caller(hermes_home, monkeypatch):
    exits = []

    class Stuck:
        async def __aenter__(self):
            raise FenceTimeout("held")

        async def __aexit__(self, *exc):
            exits.append(1)
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: Stuck())
    handle = _oauth_with_expiry(hermes_home, seconds_left=10)
    with pytest.raises(FenceTimeout):
        _run_guard(handle)
    assert exits == []


def test_refresh_guard_frees_its_in_process_lock_after_a_fence_timeout(hermes_home, monkeypatch):
    class Stuck:
        async def __aenter__(self):
            raise FenceTimeout("held")

        async def __aexit__(self, *exc):
            return None
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: Stuck())
    handle = _oauth_with_expiry(hermes_home, seconds_left=10)

    async def twice():
        for _ in range(2):
            with pytest.raises(FenceTimeout):
                await asyncio.wait_for(_enter(handle), 2)

    async def _enter(h):
        async with h.refresh_guard():
            pass
    asyncio.run(twice())


def test_refresh_guard_saves_metadata_the_file_lacks(hermes_home):
    from mcp.shared.auth import OAuthMetadata
    handle = _oauth_with_expiry(hermes_home, seconds_left=3000)
    handle.httpx_auth().context.oauth_metadata = OAuthMetadata.model_validate(METADATA)
    _run_guard(handle)
    path, _ = token_paths(str(hermes_home))
    assert FileTokenStorage(path, OAUTH.mcp_url).read().oauth_metadata["token_endpoint"] == METADATA["token_endpoint"]


# login and logout.

def test_logout_deletes_the_token_file_and_reports_it(hermes_home):
    _write_pair(hermes_home, 3600)
    assert logout(OAUTH, hermes_home=str(hermes_home)) is True
    assert logout(OAUTH, hermes_home=str(hermes_home)) is False
    assert not token_paths(str(hermes_home))[0].exists()


def test_login_refuses_a_token_mode_profile(hermes_home):
    with pytest.raises(AuthConfigError, match="oauth"):
        login(TOKEN, hermes_home=str(hermes_home), open_browser=False, read_pasted=lambda: "")


class FakeAuthServer:
    """The engine's /mcp behind a bearer check, plus discovery, DCR and the token endpoint."""

    def __init__(self):
        from fakes import FakeEngine
        self.engine = FakeEngine()
        self.issued = []
        self.registrations = 0
        self.codes = {}

    def client_factory(self, **kwargs):
        kwargs.pop("transport", None)
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app), **kwargs)

    async def app(self, scope, receive, send):
        if scope["type"] != "http":
            return
        path, headers = scope["path"], {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        raw = b""
        while True:
            m = await receive()
            raw += m.get("body", b"")
            if not m.get("more_body"):
                break
        base = "http://fake.lumberroom.test"
        if path == "/mcp":
            if headers.get("authorization") not in {f"Bearer {t}" for t in self.issued}:
                return await self._send(send, 401, {"error": "invalid_token"}, [
                    (b"www-authenticate", f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"'.encode())])
            return await self.engine.app({**scope}, _replay(raw), send)
        if path.startswith("/.well-known/oauth-protected-resource"):
            return await self._send(send, 200, {"resource": f"{base}/mcp", "authorization_servers": [base]})
        if path.startswith("/.well-known/oauth-authorization-server"):
            return await self._send(send, 200, {**METADATA, "registration_endpoint": f"{base}/oauth/register",
                                                "code_challenge_methods_supported": ["S256"],
                                                "grant_types_supported": ["authorization_code", "refresh_token"]})
        if path == "/oauth/register":
            self.registrations += 1
            body = json.loads(raw)
            return await self._send(send, 201, {**body, "client_id": "c-1"})
        if path == "/oauth/token":
            form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            if form.get("grant_type") != "authorization_code" or form.get("code") != "good-code":
                return await self._send(send, 400, {"error": "invalid_grant"})
            token = f"at-{len(self.issued) + 1}"
            self.issued.append(token)
            return await self._send(send, 200, {"access_token": token, "token_type": "Bearer", "expires_in": 3600,
                                                "refresh_token": "rt-1"})
        return await self._send(send, 404, {"error": "not found"})

    @staticmethod
    async def _send(send, status, payload, extra=None):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")] + (extra or [])})
        await send({"type": "http.response.body", "body": json.dumps(payload).encode()})


def _replay(raw):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}
    return receive


def _login_via_paste(hermes_home, server, monkeypatch, *, code="good-code"):
    monkeypatch.setattr(auth_mod, "_http_client", server.client_factory)
    lines = []

    def paste():
        state = parse_qs(urlsplit(lines[-1]).query)["state"][0]
        return f"http://127.0.0.1:1/callback?code={code}&state={state}"
    cfg = dataclasses.replace(OAUTH, oauth_callback_port=free_port())
    login(cfg, hermes_home=str(hermes_home), open_browser=False, read_pasted=paste, out=lines.append)
    return cfg, lines


def test_login_stores_a_token_pair_client_and_metadata(hermes_home, monkeypatch):
    server = FakeAuthServer()
    cfg, lines = _login_via_paste(hermes_home, server, monkeypatch)
    stored = FileTokenStorage(token_paths(str(hermes_home))[0], cfg.mcp_url).read()
    assert lines[0] == "Open this URL to sign in:"
    assert stored.tokens["access_token"] == "at-1" and stored.client_info["client_id"] == "c-1"
    assert stored.oauth_metadata["token_endpoint"] == METADATA["token_endpoint"]
    assert stored.expires_at > time.time() + 3000
    assert any(r.body and r.body.get("method") == "tools/list" for r in server.engine.requests)


def test_login_signs_in_again_even_with_a_valid_pair_on_disk(hermes_home, monkeypatch):
    server = FakeAuthServer()
    _login_via_paste(hermes_home, server, monkeypatch)
    _, lines = _login_via_paste(hermes_home, server, monkeypatch)
    stored = FileTokenStorage(token_paths(str(hermes_home))[0], OAUTH.mcp_url).read()
    assert lines[0] == "Open this URL to sign in:" and stored.tokens["access_token"] == "at-2"
    assert server.registrations == 1


def test_a_refused_code_fails_the_login_and_keeps_the_old_pair(hermes_home, monkeypatch):
    server = FakeAuthServer()
    _login_via_paste(hermes_home, server, monkeypatch)
    with pytest.raises(LoginFailed):
        _login_via_paste(hermes_home, server, monkeypatch, code="bad-code")
    assert FileTokenStorage(token_paths(str(hermes_home))[0], OAUTH.mcp_url).read().tokens["access_token"] == "at-1"


def test_a_second_authorization_in_one_flow_gets_a_fresh_listener_and_reader(hermes_home, monkeypatch):
    # The SDK authorizes again on a 403 insufficient_scope step-up, through the same handlers.
    monkeypatch.setattr(auth_mod, "_CALLBACK_TIMEOUT_S", 2.0)
    pasted = iter(["http://127.0.0.1:1/callback?code=c-1&state=s", "http://127.0.0.1:1/callback?code=c-2&state=s"])
    _, handle, _ = interactive(hermes_home, read_pasted=lambda: next(pasted))
    first = _sign_in_by_paste(handle, "http://x/authorize")
    second = _sign_in_by_paste(handle, "http://x/authorize")
    assert (first.code, second.code) == ("c-1", "c-2")

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
from lumberroom_hermes.tokens import FenceTimeout, FileTokenStorage, RefreshFence, token_paths

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


def test_oauth_seeds_the_pair_and_metadata_from_disk(hermes_home):
    from mcp.shared.auth import OAuthToken
    path, _ = token_paths(str(hermes_home))
    path.parent.mkdir(parents=True)
    s = FileTokenStorage(path, OAUTH.mcp_url)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=30, refresh_token="r")))
    s.save_metadata(METADATA)
    handle = build_auth(OAUTH, hermes_home=str(hermes_home))
    ctx = handle.httpx_auth().context
    assert ctx.current_tokens.refresh_token == "r"
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

    def __enter__(self):
        self.log.append("fence in")
        return self

    def __exit__(self, *exc):
        self.log.append("fence out")


def _write_pair(hermes_home, seconds_left, access="a", refresh="r"):
    from mcp.shared.auth import OAuthToken
    path, _ = token_paths(str(hermes_home))
    path.parent.mkdir(parents=True, exist_ok=True)
    s = FileTokenStorage(path, OAUTH.mcp_url)
    asyncio.run(s.set_tokens(OAuthToken(access_token=access, token_type="Bearer", expires_in=seconds_left,
                                        refresh_token=refresh)))
    return s


def _stored(hermes_home):
    return FileTokenStorage(token_paths(str(hermes_home))[0], OAUTH.mcp_url).read()


def _seed(hermes_home, monkeypatch, seconds_left, *, known_to_server=True):
    """A pair the SDK can refresh, and the engine-like server that issued it."""
    from mcp.shared.auth import OAuthClientInformationFull
    server = FakeAuthServer()
    monkeypatch.setattr(auth_mod, "_http_client", server.client_factory)
    s = _write_pair(hermes_home, seconds_left, access="at-1", refresh="rt-1")
    asyncio.run(s.set_client_info(OAuthClientInformationFull(
        client_id="c-1", redirect_uris=["http://127.0.0.1:47631/callback"], token_endpoint_auth_method="none")))
    s.save_metadata(METADATA)
    if known_to_server:
        server.issued.append("at-1")
        server.live.add("rt-1")
    return server


def _oauth_with_expiry(hermes_home, monkeypatch, seconds_left):
    server = _seed(hermes_home, monkeypatch, seconds_left)
    return build_auth(OAUTH, hermes_home=str(hermes_home)), server


def _run_guard(handle, log=None):
    async def go():
        async with handle.refresh_guard():
            if log is not None:
                log.append("body")
    asyncio.run(go())


async def _enter(handle):
    async with handle.refresh_guard():
        return handle.httpx_auth().context.current_tokens.access_token


def test_refresh_guard_skips_the_fence_while_the_token_is_fresh(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=3000)
    _run_guard(handle)
    assert (entered, server.presented) == ([], [])


def test_refresh_guard_takes_the_fence_inside_the_skew(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    handle, _ = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=10)
    _run_guard(handle)
    assert entered == [1]


def test_refresh_guard_takes_the_fence_when_the_file_records_no_expiry(hermes_home, monkeypatch):
    entered = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: entered.append(1) or _NoFence())
    handle, _ = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=None)
    _run_guard(handle)
    assert entered == [1]


def test_a_pair_inside_the_skew_window_is_refreshed_before_the_guarded_request(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=30)
    assert asyncio.run(_enter(handle)) == "at-2"
    assert server.presented == ["rt-1"]
    assert _stored(hermes_home).tokens["refresh_token"] == "rt-2"


def test_a_pair_with_no_recorded_expiry_is_refreshed_and_gains_one(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=None)
    asyncio.run(_enter(handle))
    assert server.presented == ["rt-1"]
    assert _stored(hermes_home).expires_at > time.time() + 3000


def test_the_sdk_flow_outside_the_guard_never_refreshes_on_its_own(hermes_home, monkeypatch):
    # GET stream reconnects and the DELETE on close run this flow with no guard around them, so a
    # refresh the SDK started there would be one no fence covers.
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)

    async def first_request():
        probe = httpx2.Request("POST", OAUTH.mcp_url)
        flow = handle.httpx_auth().async_auth_flow(probe)
        try:
            return await flow.__anext__() is probe
        finally:
            await flow.aclose()
    assert asyncio.run(first_request()) is True
    assert server.presented == []


def test_a_refresh_outlives_a_caller_that_reached_its_deadline(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    server.rotate_delay_s = 0.5

    async def go():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_enter(handle), 0.1)
        await asyncio.sleep(0.8)
        return await _enter(handle)
    assert asyncio.run(go()) == "at-2"
    assert server.presented == ["rt-1"] and not server.family_revoked
    assert _stored(hermes_home).tokens["refresh_token"] == "rt-2"


def test_the_fence_stays_held_until_the_rotated_pair_is_on_disk(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    server.rotate_delay_s = 0.5
    on_disk_at_release = []

    class Recording(_NoFence):
        async def __aexit__(self, *exc):
            on_disk_at_release.append(_stored(hermes_home).tokens["refresh_token"])
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: Recording())

    async def go():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_enter(handle), 0.1)
        await asyncio.sleep(0.8)
    asyncio.run(go())
    assert on_disk_at_release == ["rt-2"]


def test_the_fence_is_released_before_the_request_once_the_pair_is_refreshed(hermes_home, monkeypatch):
    log = []
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: _NoFence(log))
    handle, _ = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=10)
    _run_guard(handle, log)
    assert log == ["fence in", "fence out", "body"]


def test_a_refresh_answered_5xx_drops_the_refresh_token_and_asks_for_sign_in(hermes_home, monkeypatch):
    # The engine may have spent the token before it answered, so presenting it again risks the
    # replay that revokes the family.
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    server.token_statuses = [502]
    for _ in range(2):
        with pytest.raises(LoginRequired):
            asyncio.run(_enter(handle))
    assert "refresh_token" not in _stored(hermes_home).tokens
    assert server.presented == ["rt-1"] and not server.family_revoked


def test_a_refresh_answered_5xx_inside_the_skew_window_keeps_serving_the_live_token(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=30)
    server.token_statuses = [503]
    assert asyncio.run(_enter(handle)) == "at-1"
    assert asyncio.run(_enter(handle)) == "at-1"
    assert "refresh_token" not in _stored(hermes_home).tokens


def test_a_refused_refresh_is_login_required_and_is_not_presented_again(hermes_home, monkeypatch):
    server = _seed(hermes_home, monkeypatch, -5, known_to_server=False)
    handle = build_auth(OAUTH, hermes_home=str(hermes_home))
    for _ in range(2):
        with pytest.raises(LoginRequired):
            asyncio.run(_enter(handle))
    assert server.presented == ["rt-1"]


def test_a_flow_on_the_shared_provider_never_runs_a_grant_while_a_refresh_is_in_flight(hermes_home, monkeypatch):
    # The grant runs on a private provider. A GET reconnect or the DELETE on close runs the shared
    # provider's flow outside any guard; if it could refresh, its lost answer or failed save would
    # escape the handling that keeps a spent token from being presented again.
    import httpx2
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    server.rotate_delay_s = 0.3
    shared = handle.httpx_auth()

    async def go():
        refresh = asyncio.ensure_future(_enter(handle))
        await asyncio.sleep(0.1)
        assert shared.context.token_expiry_time is None
        probe = httpx2.Request("DELETE", OAUTH.mcp_url)
        flow = shared.async_auth_flow(probe)
        first = await flow.__anext__()
        await flow.aclose()
        token = await refresh
        return first is probe, token
    sent_probe, token = asyncio.run(go())
    assert sent_probe
    assert token == "at-2"
    assert server.presented == ["rt-1"]


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


def test_refresh_guard_adopts_a_pair_a_peer_refreshed_while_it_waited(hermes_home, monkeypatch):
    log = []
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=10)

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
    assert server.presented == []


def test_refresh_guard_reloads_when_a_peer_rewrote_the_file(hermes_home, monkeypatch):
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: _NoFence())
    handle, _ = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=3000)
    time.sleep(0.01)
    _write_pair(hermes_home, 3600, access="peer")
    _run_guard(handle)
    assert handle.httpx_auth().context.current_tokens.access_token == "peer"


def test_refresh_guard_forgets_the_pair_after_a_peer_logged_out(hermes_home, monkeypatch):
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: _NoFence())
    handle, _ = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=3000)
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
    handle, _ = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=10)
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: Stuck())
    with pytest.raises(FenceTimeout):
        _run_guard(handle)
    assert exits == []


def test_refresh_guard_frees_its_in_process_lock_after_a_fence_timeout(hermes_home, monkeypatch):
    class Stuck:
        async def __aenter__(self):
            raise FenceTimeout("held")

        async def __aexit__(self, *exc):
            return None
    handle, _ = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=10)
    monkeypatch.setattr(auth_mod, "RefreshFence", lambda *a, **k: Stuck())

    async def twice():
        for _ in range(2):
            with pytest.raises(FenceTimeout):
                await asyncio.wait_for(_enter(handle), 2)
    asyncio.run(twice())


def test_refresh_guard_saves_metadata_the_file_lacks(hermes_home):
    from mcp.shared.auth import OAuthMetadata
    _write_pair(hermes_home, 3000)
    handle = build_auth(OAUTH, hermes_home=str(hermes_home))
    handle.httpx_auth().context.oauth_metadata = OAuthMetadata.model_validate(METADATA)
    _run_guard(handle)
    assert _stored(hermes_home).oauth_metadata["token_endpoint"] == METADATA["token_endpoint"]


# settle, a lost answer and a refused write.

def test_settle_waits_for_a_refresh_still_in_flight(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    server.rotate_delay_s = 0.5

    async def go():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_enter(handle), 0.1)
        await handle.settle(5.0)
    # asyncio.run cancels whatever is still pending, so only a settled refresh reaches the disk.
    asyncio.run(go())
    assert _stored(hermes_home).tokens["refresh_token"] == "rt-2"


def test_settle_stops_waiting_at_its_timeout_and_leaves_the_refresh_running(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    server.rotate_delay_s = 0.8

    async def go():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_enter(handle), 0.1)
        started = time.monotonic()
        await handle.settle(0.2)
        waited = time.monotonic() - started
        await asyncio.sleep(1.0)
        return waited
    assert asyncio.run(go()) < 0.5
    assert _stored(hermes_home).tokens["refresh_token"] == "rt-2"


def test_settle_with_nothing_in_flight_returns_at_once(hermes_home, monkeypatch):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    handles = [build_auth(TOKEN, hermes_home=str(hermes_home)), _oauth_with_expiry(hermes_home, monkeypatch, 3000)[0]]
    started = time.monotonic()
    for handle in handles:
        asyncio.run(handle.settle(5.0))
    assert time.monotonic() - started < 0.5


class _LosesTheAnswer(httpx2.AsyncBaseTransport):
    """The grant reaches the server, which rotates, and the answer never makes it back."""

    def __init__(self, server):
        self._inner = httpx2.ASGITransport(app=server.app)

    async def handle_async_request(self, request):
        response = await self._inner.handle_async_request(request)
        await response.aread()
        raise httpx2.ReadTimeout("the answer was lost", request=request)


class _NeverConnects(httpx2.AsyncBaseTransport):
    async def handle_async_request(self, request):
        raise httpx2.ConnectError("connection refused", request=request)


def _through(monkeypatch, transport):
    monkeypatch.setattr(auth_mod, "_http_client", lambda **kw: httpx2.AsyncClient(transport=transport, **kw))


def test_a_refresh_whose_answer_was_lost_is_login_required_and_the_refresh_token_is_not_presented_again(
        hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    _through(monkeypatch, _LosesTheAnswer(server))
    with pytest.raises(LoginRequired):
        asyncio.run(_enter(handle))
    monkeypatch.setattr(auth_mod, "_http_client", server.client_factory)
    with pytest.raises(LoginRequired):
        asyncio.run(_enter(handle))
    assert server.presented == ["rt-1"] and not server.family_revoked


def test_a_peer_does_not_present_a_refresh_token_whose_answer_was_lost(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    _through(monkeypatch, _LosesTheAnswer(server))
    with pytest.raises(LoginRequired):
        asyncio.run(_enter(handle))
    monkeypatch.setattr(auth_mod, "_http_client", server.client_factory)
    with pytest.raises(LoginRequired):
        asyncio.run(_enter(build_auth(OAUTH, hermes_home=str(hermes_home))))
    assert "refresh_token" not in _stored(hermes_home).tokens
    assert server.presented == ["rt-1"] and not server.family_revoked


def test_a_lost_answer_the_disk_would_not_record_still_keeps_this_process_from_presenting_the_token(
        hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    _through(monkeypatch, _LosesTheAnswer(server))
    _refuse_writes(monkeypatch, handle)
    with pytest.raises(LoginRequired):
        asyncio.run(_enter(handle))
    monkeypatch.setattr(auth_mod, "_http_client", server.client_factory)
    with pytest.raises(LoginRequired):
        asyncio.run(_enter(handle))
    assert server.presented == ["rt-1"] and not server.family_revoked


def test_a_refresh_that_outlasts_the_fence_timeout_is_login_required(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    server.rotate_delay_s = 1.0
    monkeypatch.setattr(auth_mod, "FENCE_TIMEOUT_S", 0.2)
    with pytest.raises(LoginRequired):
        asyncio.run(_enter(handle))
    assert "refresh_token" not in _stored(hermes_home).tokens


def test_a_refresh_that_never_connected_keeps_the_refresh_token(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    _through(monkeypatch, _NeverConnects())
    with pytest.raises(auth_mod.RefreshUnavailable):
        asyncio.run(_enter(handle))
    assert _stored(hermes_home).tokens["refresh_token"] == "rt-1"


def _refuse_writes(monkeypatch, handle):
    import errno

    def full(record):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(handle.storage, "_write", full)


def test_a_rotated_pair_the_disk_refused_stays_in_memory_and_is_saved_on_a_later_guard(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    _refuse_writes(monkeypatch, handle)
    for _ in range(2):
        with pytest.raises(auth_mod.TokenSaveFailed, match="No space left"):
            asyncio.run(_enter(handle))
        ctx = handle.httpx_auth().context
        # No expiry, so an SDK flow outside the guard cannot start a refresh of its own.
        assert (ctx.current_tokens.refresh_token, ctx.token_expiry_time) == ("rt-2", None)
    monkeypatch.undo()
    monkeypatch.setattr(auth_mod, "_http_client", server.client_factory)
    assert asyncio.run(_enter(handle)) == "at-2"
    stored = _stored(hermes_home)
    assert stored.tokens["refresh_token"] == "rt-2" and stored.expires_at > time.time() + 3000
    assert server.presented == ["rt-1"] and not server.family_revoked


def test_a_pair_signed_in_while_a_rotated_pair_waited_to_be_saved_wins(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    _refuse_writes(monkeypatch, handle)
    with pytest.raises(auth_mod.TokenSaveFailed):
        asyncio.run(_enter(handle))
    monkeypatch.undo()
    _write_pair(hermes_home, 3600, access="login", refresh="login-r")
    assert asyncio.run(_enter(handle)) == "login"
    assert _stored(hermes_home).tokens["refresh_token"] == "login-r"


def test_a_peer_never_presents_the_spent_token_while_a_rotated_pair_waits_to_be_saved(hermes_home, monkeypatch):
    # The disk refused the rotated pair, so oauth.json still held the spent refresh token. A peer
    # process, or the next one after a restart, would present it and the engine would revoke the
    # family, the in-memory pair included.
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    _refuse_writes(monkeypatch, handle)
    with pytest.raises(auth_mod.TokenSaveFailed):
        asyncio.run(_enter(handle))
    peer = build_auth(OAUTH, hermes_home=str(hermes_home))
    with pytest.raises(LoginRequired):
        asyncio.run(_enter(peer))
    assert server.presented == ["rt-1"] and not server.family_revoked


def test_settle_saves_a_rotated_pair_the_disk_refused_once_the_disk_recovers(hermes_home, monkeypatch):
    handle, server = _oauth_with_expiry(hermes_home, monkeypatch, seconds_left=-5)
    _refuse_writes(monkeypatch, handle)
    with pytest.raises(auth_mod.TokenSaveFailed):
        asyncio.run(_enter(handle))
    monkeypatch.undo()
    asyncio.run(handle.settle(2.0))
    stored = _stored(hermes_home)
    assert stored.tokens["refresh_token"] == "rt-2" and stored.client_info is not None
    assert server.presented == ["rt-1"]


# The refresh through the bridge, end to end against the engine-like server.

def _bridge(hermes_home, server):
    from lumberroom_hermes.bridge import Bridge
    b = Bridge(OAUTH, build_auth(OAUTH, hermes_home=str(hermes_home)), session_id="s",
               client_factory=server.client_factory)
    b.start()
    return b


def test_no_refresh_token_is_presented_twice_when_the_token_endpoint_is_slower_than_the_call_bound(
        hermes_home, monkeypatch):
    server = _seed(hermes_home, monkeypatch, -5)
    server.rotate_delay_s = 0.8
    b = _bridge(hermes_home, server)
    try:
        first = b.call_many([("memory_search", {"query": "q"})], invocation="hook", timeout=0.3)[0]
        time.sleep(1.2)
        second = b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    finally:
        b.close()
    assert first.kind != "ok" and second.kind == "ok"
    assert len(server.presented) == len(set(server.presented)) and not server.family_revoked


def test_two_bridges_on_one_profile_refresh_the_pair_once(hermes_home, monkeypatch):
    server = _seed(hermes_home, monkeypatch, -5)
    server.rotate_delay_s = 0.2
    bridges = [_bridge(hermes_home, server), _bridge(hermes_home, server)]
    results = []
    try:
        threads = [threading.Thread(target=lambda b=b: results.append(
            b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind)) for b in bridges]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    finally:
        for b in bridges:
            b.close()
    assert results == ["ok", "ok"]
    assert server.presented == ["rt-1"]


def test_a_refresh_answered_5xx_through_the_bridge_reports_login_required(hermes_home, monkeypatch):
    server = _seed(hermes_home, monkeypatch, -5)
    server.token_statuses = [502]
    b = _bridge(hermes_home, server)
    try:
        kinds = [b.call("memory_search", {"query": "q"}, invocation="hook", timeout=5).kind for _ in range(2)]
    finally:
        b.close()
    assert kinds == ["login_required", "login_required"]
    assert not server.family_revoked


def test_closing_the_bridge_mid_refresh_lands_the_rotated_pair_for_the_next_process(hermes_home, monkeypatch):
    server = _seed(hermes_home, monkeypatch, -5)
    server.rotate_delay_s = 1.0
    b = _bridge(hermes_home, server)
    first = b.call_many([("memory_search", {"query": "q"})], invocation="hook", timeout=0.3)[0]
    started = time.monotonic()
    b.close()
    took = time.monotonic() - started
    assert _stored(hermes_home).tokens["refresh_token"] == "rt-2"
    nxt = _bridge(hermes_home, server)
    try:
        second = nxt.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    finally:
        nxt.close()
    assert first.kind != "ok" and second.kind == "ok" and took < 5.2
    assert server.presented == ["rt-1"] and not server.family_revoked


def test_a_refresh_still_unanswered_when_close_gives_up_is_not_presented_again(hermes_home, monkeypatch):
    server = _seed(hermes_home, monkeypatch, -5)
    server.rotate_delay_s = 30.0
    b = _bridge(hermes_home, server)
    b.call_many([("memory_search", {"query": "q"})], invocation="hook", timeout=0.3)
    b.close(timeout=4.5)
    nxt = _bridge(hermes_home, server)
    try:
        second = nxt.call("memory_search", {"query": "q"}, invocation="hook", timeout=5)
    finally:
        nxt.close()
    assert second.kind == "login_required"
    assert server.presented == ["rt-1"] and not server.family_revoked


# login and logout.

def test_logout_deletes_the_token_file_and_reports_it(hermes_home):
    _write_pair(hermes_home, 3600)
    assert logout(OAUTH, hermes_home=str(hermes_home)) is True
    assert logout(OAUTH, hermes_home=str(hermes_home)) is False
    assert not token_paths(str(hermes_home))[0].exists()


def test_logout_waits_for_a_peer_holding_the_refresh_fence(hermes_home):
    # A peer mid-refresh writes the rotated pair back, so a logout that did not wait would be undone.
    _write_pair(hermes_home, 3600)
    path, lock = token_paths(str(hermes_home))
    fence = RefreshFence(lock, timeout_s=5)
    asyncio.run(fence.__aenter__())
    done = threading.Event()
    t = threading.Thread(target=lambda: (logout(OAUTH, hermes_home=str(hermes_home)), done.set()))
    t.start()
    try:
        assert not done.wait(0.3) and path.exists()
    finally:
        asyncio.run(fence.__aexit__(None, None, None))
    t.join(5)
    assert done.is_set() and not path.exists()


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
        # The refresh grant rotates the way the engine does: a spent token presented again
        # revokes the whole family (src/authserver/routes.rs:779-790).
        self.live, self.spent, self.presented = set(), set(), []
        self.family_revoked = False
        self.token_statuses = []      # answered before the grant is read, as a proxy would
        self.rotate_delay_s = 0.0     # the engine has rotated but not yet answered

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
            if form.get("grant_type") == "refresh_token":
                return await self._refresh(send, form.get("refresh_token"))
            if form.get("grant_type") != "authorization_code" or form.get("code") != "good-code":
                return await self._send(send, 400, {"error": "invalid_grant"})
            token = f"at-{len(self.issued) + 1}"
            self.issued.append(token)
            self.live.add("rt-1")
            return await self._send(send, 200, {"access_token": token, "token_type": "Bearer", "expires_in": 3600,
                                                "refresh_token": "rt-1"})
        return await self._send(send, 404, {"error": "not found"})

    async def _refresh(self, send, presented):
        self.presented.append(presented)
        if self.token_statuses:
            return await self._send(send, self.token_statuses.pop(0), {"error": "temporarily_unavailable"})
        if presented in self.spent:
            self.family_revoked = True
            self.live.clear()
            self.issued.clear()
            return await self._send(send, 400, {"error": "invalid_grant"})
        if presented not in self.live:
            return await self._send(send, 400, {"error": "invalid_grant"})
        self.live.discard(presented)
        self.spent.add(presented)
        access, refresh = f"at-{len(self.spent) + 1}", f"rt-{len(self.spent) + 1}"
        self.issued.append(access)
        self.live.add(refresh)
        if self.rotate_delay_s:
            await asyncio.sleep(self.rotate_delay_s)
        return await self._send(send, 200, {"access_token": access, "token_type": "Bearer", "expires_in": 3600,
                                            "refresh_token": refresh})

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


_PIPED_SIGN_IN = """
import asyncio, sys, time
sys.path.insert(0, sys.argv[1])
import conftest
from lumberroom_hermes.auth import _BrowserSignIn

sign_in = _BrowserSignIn(0, open_browser=False, read_pasted=None, out=print)
asyncio.run(sign_in.redirect("http://127.0.0.1:1/oauth/authorize?state=piped"))
time.sleep(30)
"""


def test_the_authorize_url_reaches_a_piped_stdout_while_the_login_waits():
    # Hermes v2026.9.21 leaves a piped stdout block-buffered, so a plain print of the URL stayed in
    # the buffer while login blocked on the browser or a paste, and a scripted login never saw it.
    import os
    import pathlib
    import subprocess
    import sys

    tests_dir = str(pathlib.Path(__file__).resolve().parent)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    child = subprocess.Popen([sys.executable, "-c", _PIPED_SIGN_IN, tests_dir], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, cwd=tests_dir, env=env)
    lines: list[bytes] = []

    def read_two() -> None:
        for _ in range(2):
            lines.append(child.stdout.readline())

    reader = threading.Thread(target=read_two, daemon=True)
    reader.start()
    try:
        reader.join(timeout=15)
    finally:
        child.kill()
        child.wait()
    assert any(b"state=piped" in line for line in lines), lines

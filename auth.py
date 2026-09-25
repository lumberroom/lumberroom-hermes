"""Static bearer or OAuth through the mcp SDK, behind one handle the bridge uses."""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import queue
import threading
import time
import webbrowser
from typing import Any, Callable, Protocol
from urllib.parse import parse_qs, urlsplit

from .config import TOKEN_ENV, LumberroomConfig
from .tokens import REFRESH_SKEW_S, FileTokenStorage, RefreshFence, StoredOAuth, token_paths

CLIENT_NAME = "Hermes Agent (lumberroom)"

_CALLBACK_TIMEOUT_S = 300.0   # design target: how long login waits for the browser or a paste
_LOGIN_HINT = "run `hermes lumberroom login` to sign in"


class LoginRequired(Exception):
    """OAuth needs a browser and this process may not open one."""


class AuthConfigError(Exception):
    """The credential the config names is missing."""


class LoginFailed(Exception):
    """The authorization server or the pasted URL refused the login."""


class _NoCode(LoginFailed):
    """A callback URL with neither a code nor an error: a stray request or a partial paste."""


class AuthHandle(Protocol):
    mode: str

    def headers(self) -> dict[str, str]: ...

    def httpx_auth(self) -> Any: ...   # httpx2.Auth or None

    def refresh_guard(self) -> contextlib.AbstractAsyncContextManager[None]: ...


def _http_client(**kwargs: Any) -> Any:
    # Tests swap this for a client on an in-process transport.
    import httpx2

    return httpx2.AsyncClient(**kwargs)


def _read_token() -> str:
    # Looked up through the module on every call so a profile scope installed after import applies.
    from agent import secret_scope

    value = secret_scope.get_secret(TOKEN_ENV)
    return value.strip() if isinstance(value, str) else ""


def token_present(cfg: LumberroomConfig) -> bool:
    """Token mode: LUMBERROOM_HERMES_TOKEN is non-empty. OAuth mode: True. No network, no file I/O."""
    if cfg.auth == "oauth":
        return True
    return bool(_read_token())


def build_auth(cfg: LumberroomConfig, *, hermes_home: str, interactive: bool = False,
               open_browser: bool = True, read_pasted: Callable[[], str] | None = None,
               out: Callable[[str], None] = print) -> AuthHandle:
    """Token mode reads the secret now, on the calling thread. Non-interactive OAuth handlers raise LoginRequired."""
    if cfg.auth == "token":
        token = _read_token()
        if not token:
            raise AuthConfigError(f"{TOKEN_ENV} is empty or unset in this profile's .env, and auth: token needs it")
        return TokenAuth(token)
    return OAuthAuth(cfg, hermes_home=hermes_home, interactive=interactive, open_browser=open_browser,
                     read_pasted=read_pasted, out=out)


def parse_callback(url: str) -> tuple[str, str | None]:
    """(code, state) from a pasted redirect URL. Raises LoginFailed on error= or a missing code."""
    result = _parse_redirect(url)
    return result.code, result.state


def _parse_redirect(url: str):
    from mcp.shared.auth import AuthorizationCodeResult

    raw = url.strip()
    query = urlsplit(raw).query
    if not query and "?" not in raw and "=" in raw:
        query = raw     # the user pasted only the query string
    params = {k: v[0] for k, v in parse_qs(query).items()}
    if "error" in params:
        detail = params.get("error_description")
        raise LoginFailed(f"the authorization server refused the sign-in: {params['error']}"
                          + (f" ({detail})" if detail else ""))
    if not params.get("code"):
        raise _NoCode("the redirect URL carries no code= parameter; paste the full address the browser landed on")
    return AuthorizationCodeResult(code=params["code"], state=params.get("state"), iss=params.get("iss"))


def login(cfg: LumberroomConfig, *, hermes_home: str, open_browser: bool,
          read_pasted: Callable[[], str], out: Callable[[str], None] = print) -> None:
    """Run the OAuth flow to a stored token pair. Prints the authorize URL on its own line."""
    if cfg.auth != "oauth":
        raise AuthConfigError("login applies to auth: oauth, and this profile sets auth: token")
    handle = OAuthAuth(cfg, hermes_home=hermes_home, interactive=True, open_browser=open_browser,
                       read_pasted=read_pasted, out=out, fresh=True)
    asyncio.run(handle.sign_in())


def logout(cfg: LumberroomConfig, *, hermes_home: str) -> bool:
    """Delete this profile's token file. True when one existed."""
    path, _ = token_paths(hermes_home)
    return FileTokenStorage(path, cfg.mcp_url).clear()


class TokenAuth:
    mode = "token"

    def __init__(self, token: str) -> None:
        self._token = token

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}"}

    def httpx_auth(self) -> None:
        return None

    @contextlib.asynccontextmanager
    async def refresh_guard(self):
        yield


def _model(cls: Any, raw: dict[str, Any] | None) -> Any:
    from pydantic import ValidationError

    if raw is None:
        return None
    try:
        return cls.model_validate(raw)
    except ValidationError:
        return None


class OAuthAuth:
    """The SDK's OAuthClientProvider, seeded from the plugin's token file and fenced across processes.

    The bridge hands httpx_auth() to its clients once, so a peer's refresh reaches this process by
    reseeding the same provider's context in place, never by swapping the provider object.
    """

    mode = "oauth"

    def __init__(self, cfg: LumberroomConfig, *, hermes_home: str, interactive: bool = False,
                 open_browser: bool = True, read_pasted: Callable[[], str] | None = None,
                 out: Callable[[str], None] = print, fresh: bool = False) -> None:
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata

        self._cfg = cfg
        self._interactive = interactive
        token_path, self._lock_path = token_paths(hermes_home)
        self.storage = FileTokenStorage(token_path, cfg.mcp_url)
        if interactive:
            flow = _BrowserSignIn(cfg.oauth_callback_port, open_browser=open_browser,
                                  read_pasted=read_pasted, out=out)
            self.redirect_handler, self.callback_handler = flow.redirect, flow.callback
        else:
            self.redirect_handler, self.callback_handler = _refuse_redirect, _refuse_callback
        metadata = OAuthClientMetadata(
            client_name=CLIENT_NAME,
            redirect_uris=[f"http://127.0.0.1:{cfg.oauth_callback_port}/callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )
        sdk_storage = _FreshSignInStorage(self.storage, self._lock_path) if fresh else self.storage
        self._provider = OAuthClientProvider(server_url=cfg.mcp_url, client_metadata=metadata,
                                             storage=sdk_storage, redirect_handler=self.redirect_handler,
                                             callback_handler=self.callback_handler)
        self._lock = asyncio.Lock()
        self._stored = StoredOAuth(cfg.mcp_url, None, None, None, None)
        self._seen_mtime = 0
        self._reload()

    def headers(self) -> dict[str, str]:
        return {}

    def httpx_auth(self) -> Any:
        return self._provider

    @contextlib.asynccontextmanager
    async def refresh_guard(self):
        await self._lock.acquire()
        locked, fence = True, None
        try:
            if self.storage.mtime_ns() != self._seen_mtime:
                self._reload()      # a peer refreshed, signed in again or logged out
            if not self._interactive and self._stored.tokens is None:
                # Fail before the request: the SDK would spend a 401, discovery and a logged
                # traceback reaching the same answer on every hook call.
                raise LoginRequired(f"lumberroom is signed out: {_LOGIN_HINT}")
            if self._due():
                pending = RefreshFence(self._lock_path)
                await pending.__aenter__()
                fence = pending     # only a fence that was entered gets exited
                self._reload()
                if not self._due():  # a peer won the refresh while this process waited
                    held, fence = fence, None
                    await held.__aexit__(None, None, None)
            if fence is None:
                self._lock.release()
                locked = False
            # A due pair keeps the fence and the in-process lock through the request, so the
            # refresh the SDK runs inside it is the only one on this profile.
            yield
        finally:
            try:
                if fence is not None:
                    await fence.__aexit__(None, None, None)
            finally:
                if locked:
                    self._lock.release()
                with contextlib.suppress(OSError):
                    self._save_discovered_metadata()

    def _due(self) -> bool:
        tokens = self._stored.tokens
        # Without a refresh token the SDK cannot refresh, so there is nothing to fence.
        if not tokens or not tokens.get("refresh_token"):
            return False
        expires_at = self._stored.expires_at
        return expires_at is None or expires_at - time.time() < REFRESH_SKEW_S

    def _reload(self) -> None:
        from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

        # mtime first: a write landing between the two reads shows up as a change next time.
        self._seen_mtime = self.storage.mtime_ns()
        stored = self.storage.read()
        self._stored = stored
        ctx = self._provider.context
        # mcp 2.0.0 loads tokens without their expiry and guesses <origin>/token for refresh
        # without metadata. Both public fields close those gaps; spec section 6.2 has the table.
        ctx.current_tokens = _model(OAuthToken, stored.tokens)
        ctx.client_info = _model(OAuthClientInformationFull, stored.client_info)
        ctx.token_expiry_time = stored.expires_at
        metadata = _model(OAuthMetadata, stored.oauth_metadata)
        if metadata is not None:
            ctx.oauth_metadata = metadata

    def _save_discovered_metadata(self) -> None:
        metadata = self._provider.context.oauth_metadata
        if metadata is None or self._stored.oauth_metadata is not None or self._stored.tokens is None:
            return
        # No reload here: this runs outside the in-process lock, and the new mtime makes the next
        # guard reload anyway.
        self.storage.save_metadata(metadata.model_dump(by_alias=True, mode="json", exclude_none=True))

    async def sign_in(self) -> None:
        """Drive one tools/list so the SDK runs the whole browser flow and persists the pair."""
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        from ._version import __version__

        headers = {"x-memory-invocation": "hook", "user-agent": f"lumberroom-hermes/{__version__}"}
        timeout = httpx2.Timeout(self._cfg.tool_timeout_s, connect=self._cfg.connect_timeout_s)
        try:
            async with _http_client(headers=headers, auth=self._provider, timeout=timeout,
                                    follow_redirects=True) as client:
                async with streamable_http_client(self._cfg.mcp_url, http_client=client) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        await session.list_tools()
        except BaseException as e:
            raise _as_login_failure(e) from e
        metadata = self._provider.context.oauth_metadata
        if metadata is not None:
            self.storage.save_metadata(metadata.model_dump(by_alias=True, mode="json", exclude_none=True))


def _leaves(e: BaseException) -> list[BaseException]:
    if isinstance(e, BaseExceptionGroup):
        return [leaf for inner in e.exceptions for leaf in _leaves(inner)]
    return [e]


def _as_login_failure(e: BaseException) -> BaseException:
    # The SDK raises inside an anyio task group, so the real reason arrives wrapped in a group.
    leaves = _leaves(e)
    for leaf in leaves:
        if isinstance(leaf, LoginFailed):
            return leaf
    for leaf in leaves:
        if isinstance(leaf, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            return leaf
    return LoginFailed(f"sign-in failed: {leaves[0]}")


class _FreshSignInStorage:
    """What login hands the SDK: no stored pair, so login always runs the browser flow.

    A user who signs in again usually wants a different consent, such as `full` for
    import-builtin, and a still-valid pair would make login a silent no-op. The stored DCR client
    is reused, and the new pair lands under the fence so a peer's refresh cannot overwrite it.
    """

    def __init__(self, storage: FileTokenStorage, lock_path: Any) -> None:
        self._storage = storage
        self._lock_path = lock_path

    async def get_tokens(self):
        return None

    async def set_tokens(self, tokens) -> None:
        async with RefreshFence(self._lock_path):
            await self._storage.set_tokens(tokens)

    async def get_client_info(self):
        return await self._storage.get_client_info()

    async def set_client_info(self, client_info) -> None:
        await self._storage.set_client_info(client_info)


async def _refuse_redirect(authorization_url: str) -> None:
    raise LoginRequired(f"lumberroom needs a browser sign-in: {_LOGIN_HINT}")


async def _refuse_callback():
    raise LoginRequired(f"lumberroom needs a browser sign-in: {_LOGIN_HINT}")


class _BrowserSignIn:
    """The interactive handlers: print and open the URL, then take the loopback redirect or a paste."""

    def __init__(self, port: int, *, open_browser: bool, read_pasted: Callable[[], str] | None,
                 out: Callable[[str], None]) -> None:
        self._port = port
        self._open_browser = open_browser
        self._read_pasted = read_pasted
        self._out = out
        self._attempt: _SignInAttempt | None = None

    async def redirect(self, authorization_url: str) -> None:
        # One attempt per redirect: the SDK authorizes again on a 403 step-up, and a stopped
        # listener or reader from the first round would leave the second waiting out the timeout.
        attempt = self._attempt = _SignInAttempt(self._port, self._read_pasted, self._out)
        # The listener binds before the URL goes out, so a fast browser cannot beat it.
        attempt.start_listener()
        self._out("Open this URL to sign in:")
        self._out(authorization_url)
        if self._open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(authorization_url)
        attempt.start_paste_reader()

    async def callback(self):
        attempt = self._attempt
        if attempt is None:
            raise LoginFailed("the sign-in never produced an authorization URL")
        deadline = time.monotonic() + _CALLBACK_TIMEOUT_S
        try:
            while True:
                try:
                    item = attempt.results.get_nowait()
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        raise LoginFailed(f"timed out after {_CALLBACK_TIMEOUT_S:g} seconds waiting for the "
                                          "browser to return or for a pasted URL") from None
                    await asyncio.sleep(0.05)
                    continue
                if isinstance(item, BaseException):
                    raise item
                return item
        finally:
            attempt.shutdown()


class _SignInAttempt:
    """One loopback listener and one paste reader racing to deliver a single redirect."""

    def __init__(self, port: int, read_pasted: Callable[[], str] | None, out: Callable[[str], None]) -> None:
        self._port = port
        self._read_pasted = read_pasted
        self._out = out
        self.results: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._server: http.server.HTTPServer | None = None
        self._server_thread: threading.Thread | None = None

    def start_listener(self) -> None:
        from agent.memory_provider import spawn_context_thread

        try:
            server = http.server.HTTPServer(("127.0.0.1", self._port), self._handler())
        except OSError:
            self._out(f"Port {self._port} is busy, so the browser cannot hand the sign-in back here. "
                      "Paste the address the browser lands on instead.")
            return
        server.timeout = 0.2
        self._server = server
        self._server_thread = spawn_context_thread(self._serve, name="lumberroom-login-callback")
        self._server_thread.start()

    def _serve(self) -> None:
        server = self._server
        try:
            while not self._stop.is_set():
                server.handle_request()
        finally:
            server.server_close()

    def _handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        port, results = self._port, self.results

        class Callback(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if urlsplit(self.path).path != "/callback":
                    self.send_error(404)
                    return
                try:
                    outcome: Any = _parse_redirect(f"http://127.0.0.1:{port}{self.path}")
                    status, page = 200, "Signed in to lumberroom. You can close this tab."
                except _NoCode as e:
                    outcome, status, page = None, 400, f"Sign-in did not complete: {e}"
                except LoginFailed as e:
                    outcome, status, page = e, 400, f"Sign-in failed: {e}"
                body = page.encode()
                self.send_response(status)
                self.send_header("content-type", "text/plain; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if outcome is not None:
                    results.put(outcome)

            def log_message(self, format: str, *args: Any) -> None:
                return      # keeps the access log out of the user's terminal

        return Callback

    def start_paste_reader(self) -> None:
        if self._read_pasted is None:
            return
        from agent.memory_provider import spawn_context_thread

        spawn_context_thread(self._read_pastes, name="lumberroom-login-paste").start()

    def _read_pastes(self) -> None:
        # Nothing can interrupt a blocked read, so this thread is a daemon and stops mattering
        # once the attempt ends. A line it reads after that is dropped.
        while not self._stop.is_set():
            try:
                line = self._read_pasted()
            except (EOFError, OSError):
                return
            if self._stop.is_set():
                return
            if not isinstance(line, str) or not line.strip():
                # sys.stdin.readline returns "" forever at EOF; the wait keeps that from spinning.
                self._stop.wait(0.05)
                continue
            try:
                self.results.put(_parse_redirect(line))
                return
            except _NoCode as e:
                self._out(str(e))
            except LoginFailed as e:
                self.results.put(e)
                return

    def shutdown(self) -> None:
        self._stop.set()
        if self._server_thread is not None:
            self._server_thread.join(timeout=2.0)

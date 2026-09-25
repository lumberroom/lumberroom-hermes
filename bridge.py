"""One loop thread and two MCP sessions to the engine: hook calls and model calls."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Awaitable, Callable, Literal, Sequence

import httpx2
from agent.memory_provider import spawn_context_thread
from mcp import ClientSession, Implementation, MCPError
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CONNECTION_CLOSED

from ._version import __version__
from .auth import AuthHandle, LoginRequired
from .config import LumberroomConfig
from .tokens import FenceTimeout

Invocation = Literal["hook", "model"]
CallKind = Literal["ok", "tool_error", "unreachable", "timeout", "unauthorized", "login_required"]
CLIENT_INFO_NAME = "lumberroom-hermes"

# A proxy answers 502 or 503 while the engine restarts, before the request reaches it. Any other
# 5xx may come from an engine that already acted, so it maps to "timeout", which tells the model
# a write may have landed.
_NOT_FORWARDED = (502, 503)
_LOOP_SLACK_S = 0.1


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


class _HttpStatus(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class _EngineAuth(httpx2.Auth):
    """Delegates to whatever auth the handle holds now, then judges the final response only.

    The OAuth handle rebuilds its provider when a peer process refreshes, so a client that kept
    the first provider would send a revoked token. The status check waits for the inner flow to
    end because an event hook also sees the 401 that an OAuth refresh answers and retries.
    """

    def __init__(self, handle: AuthHandle, *, raise_for_status: bool) -> None:
        self._handle = handle
        self._raise = raise_for_status

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        inner = self._handle.httpx_auth()
        if inner is None:
            response = yield request
        else:
            flow = inner.async_auth_flow(request)
            try:
                outgoing = await flow.__anext__()
                while True:
                    response = yield outgoing
                    try:
                        outgoing = await flow.asend(response)
                    except StopAsyncIteration:
                        break
            finally:
                await flow.aclose()
        # The SDK turns any 4xx or 5xx into one generic JSON-RPC error and drops the status, so
        # the status leaves here as an exception that ends the session with a readable cause.
        if self._raise and (response.status_code == 401 or response.status_code >= 500):
            raise _HttpStatus(response.status_code)


@dataclass(eq=False)
class _Session:
    """One MCP session. Its owner task enters and exits every context, because anyio refuses to
    exit a cancel scope from a task other than the one that entered it."""

    invocation: Invocation
    opened: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    client: httpx2.AsyncClient | None = None
    session: ClientSession | None = None
    handshake: dict[str, Any] | None = None
    failure: BaseException | None = None
    task: asyncio.Task[None] | None = None


@dataclass(eq=False)
class _Attempt:
    session: _Session | None = None
    sent: bool = False


class _OpenFailed(Exception):
    def __init__(self, cause: BaseException | None) -> None:
        super().__init__(str(cause) if cause else "the session closed before it opened")
        self.cause = cause


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    if isinstance(exc, _OpenFailed) and exc.cause is not None:
        return _leaves(exc.cause)
    return [exc]


def _reason(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _result(kind: CallKind, error: str) -> CallResult:
    return CallResult(kind, None, "", error)


def _classify(failure: BaseException, attempt: _Attempt) -> CallResult:
    leaves = [leaf for leaf in _leaves(failure) if not isinstance(leaf, asyncio.CancelledError)] or [failure]
    for leaf in leaves:
        if isinstance(leaf, LoginRequired):
            return _result("login_required", _reason(leaf))
    for leaf in leaves:
        if isinstance(leaf, _HttpStatus) and leaf.status == 401:
            return _result("unauthorized", "HTTP 401")
    for leaf in leaves:
        if isinstance(leaf, (FenceTimeout, httpx2.ConnectError, httpx2.ConnectTimeout)):
            return _result("unreachable", _reason(leaf))
        if isinstance(leaf, _HttpStatus) and leaf.status in _NOT_FORWARDED:
            return _result("unreachable", f"HTTP {leaf.status}")
    leaf = leaves[0]
    if not attempt.sent:
        return _result("unreachable", _reason(leaf))
    if isinstance(leaf, MCPError) and leaf.code != CONNECTION_CLOSED:
        return _result("tool_error", leaf.error.message)
    if isinstance(leaf, (httpx2.TransportError, _HttpStatus, MCPError)):
        return _result("timeout", _reason(leaf))
    return _result("tool_error", f"lumberroom answered in a form the plugin could not read: {_reason(leaf)}")


def _tool_result(tool: str, raw: Any) -> CallResult:
    dump = raw.model_dump(by_alias=True, mode="json", exclude_none=True)
    text = "\n".join(c.get("text", "") for c in dump.get("content", []) if c.get("type") == "text")
    structured = dump.get("structuredContent")
    if dump.get("isError"):
        return CallResult("tool_error", structured, text, text or f"{tool} failed")
    return CallResult("ok", structured, text, None)


Maker = Callable[[_Attempt], Awaitable[Any]]


class Bridge:
    def __init__(self, cfg: LumberroomConfig, auth: AuthHandle, *, session_id: str,
                 client_factory: Callable[..., Any] | None = None) -> None:
        """client_factory(**httpx2.AsyncClient kwargs) -> httpx2.AsyncClient; tests pass the fake's."""
        self._cfg = cfg
        self._auth = auth
        self._factory = client_factory or httpx2.AsyncClient
        # Read here, on the caller's thread: a token handle may resolve its secret from the
        # profile scope, which the loop thread does not carry.
        self._base_headers = {"user-agent": f"{CLIENT_INFO_NAME}/{__version__}", **auth.headers()}
        self._session_id = session_id
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._sessions: dict[Invocation, _Session] = {}
        self._admin: httpx2.AsyncClient | None = None
        self._inflight: set[asyncio.Task[Any]] = set()

    # Synchronous side, called from Hermes threads.

    def start(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        ready = threading.Event()
        self._thread = spawn_context_thread(self._run, name="lumberroom-bridge", args=(ready,))
        self._thread.start()
        ready.wait(5.0)

    def list_tools(self, *, timeout: float) -> tuple[CallResult, ToolsListing | None]:
        [item] = self._run_bounded([self._list_once], timeout)
        return (item, None) if isinstance(item, CallResult) else item

    def call(self, tool: str, args: dict[str, Any], *, invocation: Invocation, timeout: float) -> CallResult:
        return self.call_many([(tool, args)], invocation=invocation, timeout=timeout)[0]

    def call_many(self, calls: Sequence[tuple[str, dict[str, Any]]], *, invocation: Invocation,
                  timeout: float) -> list[CallResult]:
        """Concurrent calls under one deadline, results in input order."""
        return self._run_bounded([self._call_maker(tool, args, invocation) for tool, args in calls], timeout)

    def http_json(self, method: str, path: str, body: dict[str, Any] | None, *,
                  timeout: float) -> tuple[int, Any]:
        """A plain request to the engine origin with the same auth. (status, parsed JSON or None)."""
        # Transport failures raise to the caller: the admin commands print them and exit non-zero.
        loop = self._loop
        if loop is None or self._closed:
            raise RuntimeError("the lumberroom bridge is not running")
        future = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(self._http_json(method, path, body), timeout), loop)
        try:
            return future.result(timeout + _LOOP_SLACK_S)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(f"lumberroom did not answer {method} {path} within {timeout}s") from None

    def set_session_id(self, session_id: str) -> None:
        loop = self._loop
        if loop is None or self._closed:
            self._apply_session_id(session_id)
            return
        # Queued on the loop, so every call submitted after this line sees the new id.
        try:
            loop.call_soon_threadsafe(self._apply_session_id, session_id)
        except RuntimeError:
            self._apply_session_id(session_id)

    def close(self, *, timeout: float = 2.0) -> None:
        loop, thread = self._loop, self._thread
        already = self._closed
        self._closed = True
        if loop is None or already:
            return
        deadline = time.monotonic() + timeout
        future = asyncio.run_coroutine_threadsafe(self._shutdown(timeout * 0.6), loop)
        try:
            future.result(timeout * 0.7)
        except Exception:
            future.cancel()
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))

    def _run_bounded(self, makers: list[Maker], timeout: float) -> list[Any]:
        """The loop's answers, or one stand-in per maker when the loop cannot answer in time."""
        loop = self._loop
        if loop is None or self._closed:
            return [_result("unreachable", "the lumberroom bridge is closed")] * len(makers)
        coro = self._bounded(makers, timeout)
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()
            return [_result("unreachable", "the lumberroom bridge is closed")] * len(makers)
        try:
            return future.result(timeout + _LOOP_SLACK_S)
        except (concurrent.futures.TimeoutError, concurrent.futures.CancelledError):
            # The loop missed its own deadline or closed under the call. The request may have
            # left, so the caller hears "timeout", never "nothing was stored".
            future.cancel()
            return [_result("timeout", f"no answer within {timeout}s")] * len(makers)

    # Loop side.

    def _run(self, ready: threading.Event) -> None:
        loop = self._loop
        assert loop is not None
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        try:
            loop.run_forever()
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.wait(pending, timeout=0.5))
            with contextlib.suppress(Exception):
                loop.run_until_complete(asyncio.wait_for(loop.shutdown_asyncgens(), 0.3))
            loop.close()

    def _headers(self, invocation: Invocation | None) -> dict[str, str]:
        headers = {**self._base_headers, "x-session-id": self._session_id}
        if invocation == "hook":
            headers["x-memory-invocation"] = "hook"
        return headers

    def _client(self, invocation: Invocation | None) -> httpx2.AsyncClient:
        return self._factory(
            headers=self._headers(invocation),
            auth=_EngineAuth(self._auth, raise_for_status=invocation is not None),
            timeout=httpx2.Timeout(self._cfg.tool_timeout_s, connect=self._cfg.connect_timeout_s),
            follow_redirects=True,
            base_url=self._cfg.origin,
        )

    def _apply_session_id(self, session_id: str) -> None:
        self._session_id = session_id
        for client in [s.client for s in self._sessions.values()] + [self._admin]:
            if client is not None:
                client.headers["x-session-id"] = session_id

    async def _own(self, s: _Session) -> None:
        try:
            async with contextlib.AsyncExitStack() as stack:
                s.client = await stack.enter_async_context(self._client(s.invocation))
                streams = await stack.enter_async_context(
                    streamable_http_client(self._cfg.mcp_url, http_client=s.client))
                session = await stack.enter_async_context(ClientSession(
                    streams[0], streams[1],
                    client_info=Implementation(name=CLIENT_INFO_NAME, version=__version__)))
                # L0 recorded that the engine completes the classic initialize handshake.
                handshake = await session.initialize()
                s.handshake = handshake.model_dump(by_alias=True, mode="json", exclude_none=True)
                s.session = session
                s.opened.set()
                await s.stop.wait()
        except BaseException as exc:  # the owner task is the only place that sees why a session ended
            s.failure = exc
        finally:
            s.session = None
            s.opened.set()
            s.finished.set()

    async def _ensure(self, invocation: Invocation, attempt: _Attempt) -> _Session:
        s = self._sessions.get(invocation)
        if s is None or s.stop.is_set() or s.finished.is_set():
            s = _Session(invocation)
            self._sessions[invocation] = s
            s.task = asyncio.ensure_future(self._own(s))
        attempt.session = s
        await s.opened.wait()
        if s.session is None:
            raise _OpenFailed(s.failure)
        return s

    def _retire(self, s: _Session) -> None:
        # No retry here. The next call opens a fresh session and the caller decides what to do.
        if self._sessions.get(s.invocation) is s:
            del self._sessions[s.invocation]
        s.stop.set()
        if not s.opened.is_set() and s.task is not None:
            s.task.cancel()

    async def _root_cause(self, exc: BaseException, attempt: _Attempt) -> BaseException:
        # A dead transport wakes every waiter with CONNECTION_CLOSED. The reason sits with the
        # owner task, which records it as it unwinds.
        s = attempt.session
        if isinstance(exc, MCPError) and exc.code == CONNECTION_CLOSED and s is not None:
            await s.finished.wait()
            return s.failure or exc
        return exc

    async def _failed(self, exc: BaseException, attempt: _Attempt) -> CallResult:
        failure = await self._root_cause(exc, attempt)
        if attempt.session is not None:
            self._retire(attempt.session)
        return _classify(failure, attempt)

    def _call_maker(self, tool: str, args: dict[str, Any], invocation: Invocation) -> Maker:
        async def once(attempt: _Attempt) -> CallResult:
            try:
                async with self._auth.refresh_guard():
                    s = await self._ensure(invocation, attempt)
                    attempt.sent = True
                    raw = await s.session.call_tool(tool, args)
            except Exception as exc:
                return await self._failed(exc, attempt)
            return _tool_result(tool, raw)
        return once

    async def _list_once(self, attempt: _Attempt) -> tuple[CallResult, ToolsListing | None] | CallResult:
        try:
            async with self._auth.refresh_guard():
                s = await self._ensure("hook", attempt)
                attempt.sent = True
                raw = await s.session.list_tools()
        except Exception as exc:
            return await self._failed(exc, attempt)
        tools = tuple(t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in raw.tools)
        handshake = s.handshake or {}
        return CallResult("ok", handshake, "", None), ToolsListing(tools, handshake.get("instructions"))

    async def _bounded(self, makers: list[Maker], timeout: float) -> list[Any]:
        """Every maker under one deadline. asyncio.wait returns at the deadline; wait_for would
        also sit out the SDK's shielded cancel frame, which can outlast the bound."""
        attempts = [_Attempt() for _ in makers]
        tasks = [asyncio.ensure_future(make(a)) for make, a in zip(makers, attempts)]
        self._inflight.update(tasks)
        try:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
        finally:
            self._inflight.difference_update(tasks)
        for task in pending:
            task.cancel()
        out: list[Any] = []
        for task, attempt in zip(tasks, attempts):
            if task in done and not task.cancelled() and task.exception() is None:
                out.append(task.result())
                continue
            if attempt.session is not None:
                self._retire(attempt.session)
            if attempt.sent:
                out.append(_result("timeout", f"no answer within {timeout}s"))
            else:
                out.append(_result("unreachable", f"no answer to the handshake within {timeout}s"))
        return out

    async def _http_json(self, method: str, path: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        if self._admin is None:
            self._admin = self._client(None)
        kwargs = {"json": body} if body is not None else {}
        async with self._auth.refresh_guard():
            response = await self._admin.request(method, path, **kwargs)
        try:
            parsed = response.json() if response.content else None
        except ValueError:
            parsed = None
        return response.status_code, parsed

    async def _shutdown(self, budget: float) -> None:
        for task in list(self._inflight):
            task.cancel()
        sessions = list(self._sessions.values())
        for s in sessions:
            self._retire(s)
        owners = [s.task for s in sessions if s.task is not None]
        if owners:
            _, stuck = await asyncio.wait(owners, timeout=budget)
            for task in stuck:
                task.cancel()
        if self._admin is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._admin.aclose(), 0.2)
            self._admin = None

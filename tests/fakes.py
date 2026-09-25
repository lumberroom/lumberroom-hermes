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

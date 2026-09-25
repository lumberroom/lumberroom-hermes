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

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from fakes import BASE_URL


async def test_the_sdk_client_lists_the_captured_tools_through_the_fake(fake_engine):
    async with fake_engine.client_factory(base_url=BASE_URL) as http:
        async with streamable_http_client(f"{BASE_URL}/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                listing = await session.list_tools()
    names = {t.name for t in listing.tools}
    assert {"memory_search", "memory_write", "registry_get"} <= names


async def test_the_fake_records_the_headers_a_client_sent(fake_engine):
    async with fake_engine.client_factory(base_url=BASE_URL, headers={"x-session-id": "s-1"}) as http:
        await http.post(f"{BASE_URL}/admin/ingest/runs", json={"extractor": "x"})
    assert fake_engine.requests[-1].headers["x-session-id"] == "s-1"

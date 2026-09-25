from lumberroom_hermes.schemas import load_snapshot, read_cache, select, to_hermes_schema, write_cache


def test_the_snapshot_carries_the_default_tools():
    names = {t["name"] for t in load_snapshot()["tools"]}
    assert {"memory_search", "memory_write", "registry_get"} <= names


def test_conversion_moves_input_schema_to_parameters_and_drops_schema_noise():
    out = to_hermes_schema({"name": "memory_write", "description": "d",
                            "inputSchema": {"$schema": "x", "title": "WriteArgs", "type": "object",
                                            "properties": {"content": {"type": "string"}}, "required": ["content"]}})
    assert out == {"name": "memory_write", "description": "d",
                   "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}}


def test_select_keeps_allowlist_order_and_skips_absent_names():
    tools = [{"name": n} for n in ("memory_write", "memory_search")]
    assert [t["name"] for t in select(["memory_search", "memory_forget", "memory_write"], tools)] == ["memory_search", "memory_write"]


def test_the_cache_round_trips_and_a_broken_cache_reads_as_none(tmp_path):
    write_cache(str(tmp_path), {"instructions": "i", "tools": [{"name": "memory_write"}]})
    assert read_cache(str(tmp_path))["tools"] == [{"name": "memory_write"}]
    (tmp_path / "lumberroom" / "tools_cache.json").write_text("{")
    assert read_cache(str(tmp_path)) is None

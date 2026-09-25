import pytest

from lumberroom_hermes.config import ConfigError, parse


@pytest.mark.parametrize("raw", ["https://Host.example", "https://Host.example/", "https://Host.example/mcp",
                                 "https://Host.example/mcp/", "HTTPS://Host.example"])
def test_five_spellings_of_one_engine_give_one_mcp_url(raw):
    assert parse({"base_url": raw, "auth": "token"}).mcp_url == "https://Host.example/mcp"


@pytest.mark.parametrize("block, key", [
    ({"base_url": "", "auth": "token"}, "base_url"),
    ({"base_url": "host:8787", "auth": "token"}, "base_url"),
    ({"base_url": "http://h", "auth": "bearer"}, "auth"),
    ({"base_url": "http://h", "local_platforms": ["cli", "telegram"]}, "local_platforms"),
    ({"base_url": "http://h", "local_platforms": ["api_server"]}, "local_platforms"),
    ({"base_url": "http://h", "auth": "token", "recall_limit": 50}, "recall_limit"),
    ({"base_url": "http://h", "auth": "token", "prefetch_timeout_s": 8.0}, "prefetch_timeout_s"),
    ({"base_url": "http://h", "auth": "token", "owner_user_ids": ["123"]}, "owner_user_ids"),
    ({"base_url": "http://h", "auth": "token", "recal": True}, "recal"),
])
def test_a_bad_block_names_the_key(block, key):
    with pytest.raises(ConfigError, match=key):
        parse(block)


def test_defaults_match_the_spec():
    c = parse({"base_url": "http://h", "auth": "oauth"})
    assert (c.digest_max_chars, c.recall_limit, c.recall_max_chars, c.review_interval) == (6000, 4, 1200, 10)
    assert c.local_platforms == ("cli", "tui", "desktop", "acp", "cron")
    assert c.owner_user_ids == ()


def test_an_api_server_owner_entry_is_refused():
    with pytest.raises(ConfigError, match="api_server"):
        parse({"base_url": "http://h", "auth": "token", "owner_user_ids": ["api_server:alice"]})


def test_an_empty_block_points_at_lumberroom_cloud_with_browser_sign_in():
    c = parse({})
    assert (c.mcp_url, c.auth) == ("https://mcp.lumberroom.cloud/mcp", "oauth")


def test_a_self_hosted_base_url_overrides_the_cloud_default():
    assert parse({"base_url": "http://127.0.0.1:8787", "auth": "token"}).mcp_url == "http://127.0.0.1:8787/mcp"


@pytest.mark.parametrize("base_url, hosted", [
    (None, True), ("https://mcp.lumberroom.cloud", True), ("https://LumberRoom.cloud/mcp", True),
    ("http://127.0.0.1:8787", False), ("https://lumberroom.cloud.evil.example", False),
    ("https://notlumberroom.cloud", False),
])
def test_only_lumberroom_cloud_hosts_count_as_hosted(base_url, hosted):
    block = {} if base_url is None else {"base_url": base_url}
    assert parse(block).is_hosted is hosted


def test_dreaming_review_is_off_unless_the_owner_turns_it_on():
    assert parse({}).dreaming_review is False
    assert parse({"dreaming_review": True}).dreaming_review is True


@pytest.mark.parametrize("raw", ["https://evil.example?.lumberroom.cloud", "https://evil.example#.lumberroom.cloud",
                                 "https://mcp.lumberroom.cloud@evil.example"])
def test_a_base_url_that_hides_its_real_host_is_refused(raw):
    with pytest.raises(ConfigError, match="base_url"):
        parse({"base_url": raw})

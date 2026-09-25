import pytest

from lumberroom_hermes.config import ConfigError, parse


@pytest.mark.parametrize("raw", ["https://Host.example", "https://Host.example/", "https://Host.example/mcp",
                                 "https://Host.example/mcp/", "HTTPS://Host.example"])
def test_five_spellings_of_one_engine_give_one_mcp_url(raw):
    assert parse({"base_url": raw, "auth": "token"}).mcp_url == "https://Host.example/mcp"


@pytest.mark.parametrize("block, key", [
    ({"auth": "token"}, "base_url"),
    ({"base_url": "host:8787", "auth": "token"}, "base_url"),
    ({"base_url": "http://h"}, "auth"),
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

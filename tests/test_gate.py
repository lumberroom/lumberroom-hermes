import pytest

from lumberroom_hermes.config import LumberroomConfig
from lumberroom_hermes.gate import identity_from_kwargs, session_allowed, turn_allowed

CFG = LumberroomConfig(base_url="http://h", auth="token", owner_user_ids=("telegram:42",))


def ident(**kw):
    return identity_from_kwargs({"platform": "cli", **kw})


def test_a_local_session_is_the_owner():
    assert session_allowed(ident(), CFG) and turn_allowed(ident(), None, CFG)


def test_a_gateway_dm_from_a_listed_owner_is_allowed():
    assert session_allowed(ident(platform="telegram", user_id=42, chat_type="dm"), CFG)


def test_a_gateway_dm_from_anyone_else_is_refused():
    assert not session_allowed(ident(platform="telegram", user_id="7", chat_type="dm"), CFG)


def test_a_stranger_turn_inside_an_owner_dm_is_refused():
    i = ident(platform="telegram", user_id="42", chat_type="dm")
    assert turn_allowed(i, "42", CFG) and not turn_allowed(i, "7", CFG)


def test_an_owner_dm_turn_without_an_author_is_allowed():
    assert turn_allowed(ident(platform="telegram", user_id="42", chat_type="dm"), None, CFG)


def test_a_listed_owner_turn_in_a_group_chat_is_allowed():
    i = ident(platform="telegram", user_id="7", chat_type="group")
    assert session_allowed(i, CFG) and turn_allowed(i, "42", CFG)


def test_a_non_owner_turn_in_a_group_chat_is_refused():
    i = ident(platform="telegram", user_id="42", chat_type="group")
    assert not turn_allowed(i, "7", CFG)


def test_a_group_turn_without_an_author_is_refused():
    i = ident(platform="telegram", user_id="42", chat_type="group")
    assert not turn_allowed(i, None, CFG) and not turn_allowed(i, "", CFG)


@pytest.mark.parametrize("chat_type", ["guild", "thread", "channel", "webhook", None])
def test_every_shared_chat_admits_only_a_listed_owners_turns(chat_type):
    i = ident(platform="telegram", user_id="7", chat_type=chat_type)
    assert session_allowed(i, CFG) and turn_allowed(i, "42", CFG)
    assert not turn_allowed(i, "7", CFG) and not turn_allowed(i, None, CFG)


def test_a_webhook_route_gets_memory_only_when_the_owner_lists_it():
    i = ident(platform="webhook", user_id="webhook:github", chat_type="webhook")
    listed = LumberroomConfig(base_url="http://h", auth="token", owner_user_ids=("webhook:webhook:github",))
    assert turn_allowed(i, "webhook:github", listed) and not turn_allowed(i, "webhook:github", CFG)


def test_cron_is_the_owner_by_default():
    cron = ident(platform="cron", agent_context="cron")
    assert session_allowed(cron, CFG) and turn_allowed(cron, None, CFG)


def test_removing_cron_from_local_platforms_refuses_cron_even_with_owners_listed():
    no_cron = LumberroomConfig(base_url="http://h", auth="token", owner_user_ids=("telegram:42",),
                               local_platforms=("cli", "tui", "desktop", "acp"))
    assert not session_allowed(ident(platform="cron", agent_context="cron"), no_cron)


@pytest.mark.parametrize("chat_type", ["dm", "group", None])
def test_empty_owner_ids_refuse_every_gateway(chat_type):
    empty = LumberroomConfig(base_url="http://h", auth="token")
    assert not session_allowed(ident(platform="discord", user_id="1", chat_type=chat_type), empty)

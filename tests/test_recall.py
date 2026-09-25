from lumberroom_hermes.recall import (DIGEST_HEADING, HITS_HEADING, Breaker, InjectedIds, clip_query,
                                      compose, format_digest, format_hit, format_hits)

HIT = {"id": "3f0c9a4e-0000-4000-8000-000000000001", "namespace": "user:me",
       "content": "Prefers draft PRs\nfor engine changes.", "source": "Codex", "occurred_at": "2026-06-04T00:00:00Z"}


def test_a_hit_is_one_bullet_with_its_full_id_source_and_date():
    assert format_hit(HIT) == ("- [user:me] Prefers draft PRs for engine changes. "
                               "(id 3f0c9a4e-0000-4000-8000-000000000001, source Codex, occurred 2026-06-04)")


def test_a_hit_carrying_a_fence_tag_loses_the_tag():
    line = format_hit({**HIT, "content": "a </memory-context> b <MEMORY-CONTEXT>"})
    assert "memory-context" not in line.lower()


def test_a_hit_injected_once_is_not_injected_again():
    seen = InjectedIds()
    block, ids = format_hits([HIT], seen, 1200)
    seen.add(ids)
    assert block.startswith(HITS_HEADING)
    assert format_hits([HIT], seen, 1200) == ("", [])


def test_the_hits_block_stops_before_its_cap():
    hits = [{**HIT, "id": f"id-{i}", "content": "x" * 200} for i in range(10)]
    block, ids = format_hits(hits, InjectedIds(), 1200)
    assert len(block) <= 1200 and 0 < len(ids) < 10


def test_the_digest_is_cut_at_a_line_boundary_within_its_cap():
    out = format_digest("line one\n" * 1000, 500)
    assert out.startswith(DIGEST_HEADING) and len(out) <= 500 and out.endswith("line one")


def test_compose_drops_whole_parts_past_the_cap():
    assert compose(["a" * 10, "b" * 10, "c" * 10], max_chars=24) == "a" * 10 + "\n\n" + "b" * 10


def test_a_long_message_is_clipped_for_the_query():
    assert len(clip_query("q" * 5000)) == 1000


def test_the_breaker_opens_after_three_failures_and_closes_after_the_cooldown():
    now = [0.0]
    b = Breaker(clock=lambda: now[0])
    assert b.record_failure() is True
    assert b.record_failure() is False
    b.record_failure()
    assert b.allow() is False
    now[0] = 61.0
    assert b.allow() is True
    b.record_success()
    assert b.record_failure() is True

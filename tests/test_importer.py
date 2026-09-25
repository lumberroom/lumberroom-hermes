import pytest

from lumberroom_hermes.importer import MissingGrant, import_builtin, proposals_body, read_builtin_entries


def seed(home):
    (home / "memories" / "MEMORY.md").write_text("Dev box runs Postgres on 5433.\n§\n\n§\nUse ruff.\n")
    (home / "memories" / "USER.md").write_text("Prefers terse plans.\n")


def test_entries_split_on_the_section_sign_and_land_in_their_namespaces(hermes_home):
    seed(hermes_home)
    got = [(e.file, e.text, e.namespace) for e in read_builtin_entries(str(hermes_home))]
    assert got == [("MEMORY.md", "Dev box runs Postgres on 5433.", "global"), ("MEMORY.md", "Use ruff.", "global"),
                   ("USER.md", "Prefers terse plans.", "user:me")]


def test_the_proposal_body_matches_the_engine_shape(hermes_home):
    seed(hermes_home)
    body = proposals_body(read_builtin_entries(str(hermes_home))[:1], "r-1")
    fact = body["facts"][0]
    assert body["extractor"] == "hermes-builtin-import"
    assert fact["speaker"] == "main_model" and fact["span_text"] == fact["content"]
    assert fact["source"]["run_id"] == "r-1" and len(fact["source"]["entry_uuid"]) == 64
    assert fact["source"]["file_path"].endswith("memories/MEMORY.md")


class AdminBridge:
    def __init__(self, status=200):
        self.status, self.posts = status, []

    def http_json(self, method, path, body, *, timeout):
        self.posts.append((method, path, body))
        if self.status != 200:
            return self.status, {"error": "forbidden"}
        if path == "/admin/ingest/runs":
            return 200, {"run_id": "r-1"}
        if path == "/admin/ingest/proposals":
            return 200, {"proposals_new": len(body["facts"]), "proposals_reinforced": 0, "refused": 0, "blocked": 0, "confirmations": 0, "outcomes": []}
        return 200, {}


def test_import_opens_posts_and_closes_one_run(hermes_home):
    seed(hermes_home)
    bridge = AdminBridge()
    report = import_builtin(bridge, str(hermes_home), profile="default", dry_run=False)
    assert [p[1] for p in bridge.posts] == ["/admin/ingest/runs", "/admin/ingest/proposals", "/admin/ingest/runs/r-1/close"]
    assert report.proposals_new == 3


def test_a_dry_run_posts_nothing(hermes_home):
    seed(hermes_home)
    bridge = AdminBridge()
    assert import_builtin(bridge, str(hermes_home), profile="default", dry_run=True).posted == 0
    assert bridge.posts == []


def test_a_403_is_a_missing_grant(hermes_home):
    seed(hermes_home)
    with pytest.raises(MissingGrant):
        import_builtin(AdminBridge(status=403), str(hermes_home), profile="default", dry_run=False)

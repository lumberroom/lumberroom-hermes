from lumberroom_hermes.wizard import apply_builtin_off, post_setup


def run_setup(monkeypatch, answers, secret="t-1"):
    saved, env = {}, {}
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: env.update({k: v}))
    it = iter(answers)
    config = {"memory": {}}
    post_setup("/tmp/h", config, ask=lambda _: next(it), ask_secret=lambda _: secret, out=lambda _: None)
    return saved, env


def test_self_hosted_token_setup_writes_the_block_the_secret_and_the_builtin_flags(monkeypatch):
    saved, env = run_setup(monkeypatch, ["1", "http://127.0.0.1:8787", "token", "n"])
    m = saved["memory"]
    assert m["provider"] == "lumberroom" and m["memory_enabled"] is False and m["user_profile_enabled"] is False
    assert m["lumberroom"] == {"base_url": "http://127.0.0.1:8787", "auth": "token"}
    assert env == {"LUMBERROOM_HERMES_TOKEN": "t-1"}


def test_hosted_browser_setup_needs_no_url_and_chooses_oauth(monkeypatch):
    saved, env = run_setup(monkeypatch, ["2", "1", "n", "n"])
    assert saved["memory"]["lumberroom"] == {"base_url": "https://mcp.lumberroom.cloud", "auth": "oauth"}
    assert env == {}


def test_an_empty_deployment_answer_asks_again(monkeypatch):
    saved, _ = run_setup(monkeypatch, ["", "2", "2", "n"])
    assert saved["memory"]["lumberroom"]["auth"] == "token"


def test_apply_builtin_off_reports_each_change():
    config = {"memory": {"memory_enabled": True}}
    lines = apply_builtin_off(config)
    assert "memory.memory_enabled: true -> false" in lines
    assert config["memory"]["provider"] == "lumberroom" and config["memory"]["nudge_interval"] == 0

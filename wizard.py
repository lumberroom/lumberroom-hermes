"""hermes memory setup lumberroom: pick the deployment, the credential, and turn the built-in store off."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .config import HOSTED_BASE_URL, TOKEN_ENV

_DEPLOYMENT_OPTIONS = {"1": "Self-hosted engine (enter its URL)", "2": "lumberroom.cloud"}
_HOSTED_CREDENTIAL_OPTIONS = {"1": "Sign in with a browser", "2": "Paste an API token (lr_...)"}
_BUILTIN_OFF: tuple[tuple[str, Any], ...] = (
    ("provider", "lumberroom"),
    ("memory_enabled", False),
    ("user_profile_enabled", False),
    ("nudge_interval", 0),
)


def get_config_schema() -> list[dict[str, Any]]:
    return [
        {"key": "base_url", "label": "Engine URL", "type": "string", "required": True,
         "help": "The lumberroom engine origin, such as http://127.0.0.1:8787 or https://mcp.lumberroom.cloud."},
        {"key": "auth", "label": "Auth mode", "type": "select", "required": True, "options": ["token", "oauth"],
         "help": "token reads LUMBERROOM_HERMES_TOKEN. oauth needs hermes lumberroom login."},
    ]


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def apply_builtin_off(config: dict[str, Any]) -> list[str]:
    """Set memory.provider, memory_enabled, user_profile_enabled, nudge_interval. Returns the lines changed."""
    memory = config.setdefault("memory", {})
    changes: list[str] = []
    for key, value in _BUILTIN_OFF:
        had_key = key in memory
        old = memory.get(key)
        if had_key and old == value:
            continue
        memory[key] = value
        changes.append(f"memory.{key}: {_fmt(old) if had_key else '(unset)'} -> {_fmt(value)}")
    return changes


def save_values(values: Mapping[str, Any], hermes_home: str) -> None:
    """Persist an edit to the memory.lumberroom block, such as one the dashboard panel makes."""
    from hermes_cli.config import load_config, save_config

    from .config import write_section

    config = load_config()
    write_section(config, dict(values))
    save_config(config)


def _choose(ask: Callable[[str], str], out: Callable[[str], None], options: Mapping[str, str], prompt: str) -> str:
    for key, label in options.items():
        out(f"{key}) {label}")
    while True:
        answer = ask(prompt).strip()
        if answer in options:
            return answer


def _yes(answer: str) -> bool:
    return answer.strip().lower().startswith("y")


def post_setup(hermes_home: str, config: dict[str, Any], *,
               ask: Callable[[str], str] = input,
               ask_secret: Callable[[str], str] | None = None,
               out: Callable[[str], None] = print) -> None:
    from hermes_cli.config import save_config, save_env_value

    from .config import parse as parse_config, write_section
    from .importer import read_builtin_entries

    if ask_secret is None:
        import getpass
        ask_secret = getpass.getpass

    deployment = _choose(ask, out, _DEPLOYMENT_OPTIONS, "Choose 1 or 2: ")
    if deployment == "1":
        base_url = ask("Engine URL: ").strip()
        auth_mode = ask("Auth mode, token or oauth: ").strip().lower()
    else:
        base_url = HOSTED_BASE_URL
        credential = _choose(ask, out, _HOSTED_CREDENTIAL_OPTIONS, "Choose 1 or 2: ")
        auth_mode = "oauth" if credential == "1" else "token"

    if auth_mode == "token":
        save_env_value(TOKEN_ENV, ask_secret("API token: "))
    elif _yes(ask("Log in now? [y/N] ")):
        from .auth import login

        def read_pasted() -> str:
            import sys
            return sys.stdin.readline().strip()

        login(parse_config({"base_url": base_url, "auth": auth_mode}), hermes_home=hermes_home,
              open_browser=True, read_pasted=read_pasted, out=out)

    write_section(config, {"base_url": base_url, "auth": auth_mode})
    for line in apply_builtin_off(config):
        out(line)
    save_config(config)
    out(f"memory.lumberroom.base_url -> {base_url}")
    out(f"memory.lumberroom.auth -> {auth_mode}")

    cfg = parse_config({"base_url": base_url, "auth": auth_mode})

    if _yes(ask("Run the live check now? [y/N] ")):
        from .cli import run_live_check

        run_live_check(cfg, hermes_home, out=out)

    entries = read_builtin_entries(hermes_home)
    if entries and _yes(ask(f"Import {len(entries)} entries into the review queue now? [y/N] ")):
        from .auth import build_auth
        from .bridge import Bridge
        from .importer import import_builtin

        handle = build_auth(cfg, hermes_home=hermes_home, interactive=False)
        bridge = Bridge(cfg, handle, session_id="setup-import")
        bridge.start()
        try:
            report = import_builtin(bridge, hermes_home, profile="default", dry_run=False)
        finally:
            bridge.close()
        out(f"posted {report.posted} entries: {report.proposals_new} new, {report.proposals_reinforced} reinforced")

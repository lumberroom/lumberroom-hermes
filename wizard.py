"""hermes memory setup lumberroom: pick the deployment, the credential, and turn the built-in store off."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .config import HOSTED_BASE_URL, TOKEN_ENV

_DEPLOYMENT_OPTIONS = {"1": "lumberroom.cloud", "2": "Self-hosted engine (enter its URL)"}
_DEPLOYMENT_DEFAULT = "1"
_HOSTED_CREDENTIAL_OPTIONS = {"1": "Sign in with a browser", "2": "Paste an API token (lr_...)"}
_HOSTED_CREDENTIAL_DEFAULT = "1"
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


def _choose(ask: Callable[[str], str], out: Callable[[str], None], options: Mapping[str, str], prompt: str,
           *, default: str | None = None) -> str:
    for key, label in options.items():
        out(f"{key}) {label}")
    while True:
        answer = ask(prompt).strip()
        if not answer and default is not None:
            return default
        if answer in options:
            return answer


def _yes(answer: str) -> bool:
    return answer.strip().lower().startswith("y")


def post_setup(hermes_home: str, config: dict[str, Any], *,
               ask: Callable[[str], str] = input,
               ask_secret: Callable[[str], str] | None = None,
               out: Callable[[str], None] = print) -> None:
    from hermes_cli.config import save_config, save_env_value

    from .config import ConfigError, parse as parse_config, write_section
    from .importer import read_builtin_entries

    if ask_secret is None:
        import getpass
        ask_secret = getpass.getpass

    # Parsed and re-prompted before anything touches config or .env: a bad URL or auth mode must
    # never leave the profile half-written (memory off, no working memory.lumberroom block).
    while True:
        deployment = _choose(ask, out, _DEPLOYMENT_OPTIONS, "Choose 1 or 2 [1]: ", default=_DEPLOYMENT_DEFAULT)
        if deployment == "2":
            base_url = ask("Engine URL: ").strip()
            auth_mode = ask("Auth mode, token or oauth: ").strip().lower()
        else:
            base_url = HOSTED_BASE_URL
            credential = _choose(ask, out, _HOSTED_CREDENTIAL_OPTIONS, "Choose 1 or 2 [1]: ",
                                 default=_HOSTED_CREDENTIAL_DEFAULT)
            auth_mode = "oauth" if credential == "1" else "token"
        try:
            cfg = parse_config({"base_url": base_url, "auth": auth_mode})
            break
        except ConfigError as exc:
            out(str(exc))

    if auth_mode == "token":
        save_env_value(TOKEN_ENV, ask_secret("API token: "))
    elif _yes(ask("Log in now? [y/N] ")):
        from .auth import login

        def read_pasted() -> str:
            import sys
            return sys.stdin.readline().strip()

        login(cfg, hermes_home=hermes_home, open_browser=True, read_pasted=read_pasted, out=out)

    values: dict[str, Any] = {"base_url": base_url, "auth": auth_mode}
    # Dreaming proposals are a lumberroom.cloud feature; the self-hosted path never asks and
    # never writes the key, so status's "off (not lumberroom.cloud)" line stays the true reason.
    dreaming_review = deployment == "1" and _yes(
        ask("Let Hermes review and act on lumberroom.cloud dreaming proposals? [y/N] "))
    if dreaming_review:
        values["dreaming_review"] = True

    write_section(config, values)
    for line in apply_builtin_off(config):
        out(line)
    save_config(config)
    out(f"memory.lumberroom.base_url -> {base_url}")
    out(f"memory.lumberroom.auth -> {auth_mode}")
    if dreaming_review:
        out("memory.lumberroom.dreaming_review -> true")

    if _yes(ask("Run the live check now? [y/N] ")):
        from .cli import run_live_check

        run_live_check(cfg, hermes_home, out=out)

    entries = read_builtin_entries(hermes_home)
    if entries and _yes(ask(f"Import {len(entries)} entries into the review queue now? [y/N] ")):
        import httpx2

        from .auth import AuthConfigError, LoginRequired, RefreshUnavailable, TokenSaveFailed, build_auth
        from .bridge import Bridge
        from .cli import _profile_name
        from .importer import MissingGrant, import_builtin
        from .tokens import FenceTimeout

        try:
            handle = build_auth(cfg, hermes_home=hermes_home, interactive=False)
        except AuthConfigError as exc:
            out(str(exc))
            return

        bridge = Bridge(cfg, handle, session_id="setup-import")
        bridge.start()
        try:
            report = import_builtin(bridge, hermes_home, profile=_profile_name(hermes_home), dry_run=False)
        except MissingGrant:
            out("lumberroom refused the import (403): this credential lacks mayIngest. Add "
                '"mayIngest": true to its AUTH_TOKENS entry, or use a full consent grant.')
            return
        except (RuntimeError, TimeoutError, LoginRequired, RefreshUnavailable, TokenSaveFailed,
                FenceTimeout, httpx2.TransportError) as exc:
            out(str(exc))
            return
        finally:
            bridge.close()
        out(f"posted {report.posted} entries: {report.proposals_new} new, {report.proposals_reinforced} reinforced")

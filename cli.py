"""hermes lumberroom login | logout | status | import-builtin.

Hermes imports this file during argparse setup, before any provider loads, so heavy imports wait
inside the handlers.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable

USAGE = "Usage: hermes lumberroom <login|logout|status|import-builtin>"


def register_cli(subparser: Any) -> None:
    subs = subparser.add_subparsers(dest="lumberroom_command")

    login_parser = subs.add_parser("login")
    login_parser.add_argument("--no-browser", action="store_true", dest="no_browser")

    subs.add_parser("logout")

    status_parser = subs.add_parser("status")
    status_parser.add_argument("--json", action="store_true", dest="json")

    import_parser = subs.add_parser("import-builtin")
    import_parser.add_argument("--dry-run", action="store_true", dest="dry_run")

    subparser.set_defaults(func=lumberroom_command)


def _hermes_home() -> str:
    from hermes_constants import get_hermes_home

    return str(get_hermes_home())


def _profile_name(hermes_home: str) -> str:
    from hermes_constants import profile_name_for_home

    return profile_name_for_home(hermes_home) or "default"


def _load_cfg():
    """(cfg, None) or (None, message). The message always names both required keys: a block missing
    everything raises on auth first, and a partial hint would send the user chasing one at a time."""
    from hermes_cli.config import load_config

    from .config import ConfigError, parse, section

    try:
        return parse(section(load_config())), None
    except ConfigError as exc:
        return None, (f"{exc} (set memory.lumberroom.base_url and memory.lumberroom.auth in "
                       "config.yaml, or run: hermes memory setup lumberroom)")


def lumberroom_command(args: Any) -> int:
    handlers: dict[str, Callable[[Any], int]] = {
        "login": _login, "logout": _logout, "status": _status, "import-builtin": _import_builtin,
    }
    handler = handlers.get(getattr(args, "lumberroom_command", None))
    if handler is None:
        print(USAGE)
        return 1
    return handler(args)


def _login(args: Any) -> int:
    cfg, err = _load_cfg()
    if err is not None:
        print(str(err))
        return 1
    if cfg.auth != "oauth":
        print("lumberroom login only applies when auth is oauth; this profile uses token")
        return 1

    from .auth import LoginFailed, login as run_login

    def read_pasted() -> str:
        return sys.stdin.readline().strip()

    try:
        run_login(cfg, hermes_home=_hermes_home(), open_browser=not getattr(args, "no_browser", False),
                  read_pasted=read_pasted)
    except LoginFailed as exc:
        print(str(exc))
        return 1
    print("logged in")
    return 0


def _logout(args: Any) -> int:
    cfg, err = _load_cfg()
    if err is not None:
        print(str(err))
        return 1

    from .auth import logout as run_logout
    from .tokens import FenceTimeout

    try:
        existed = run_logout(cfg, hermes_home=_hermes_home())
    except FenceTimeout as exc:
        print(str(exc))
        return 1
    print("logged out" if existed else "already logged out")
    return 0


def _oauth_credential_present(cfg, hermes_home: str) -> bool:
    """Whether oauth.json on disk holds a token pair. No network, no refresh."""
    from .tokens import FileTokenStorage, token_paths

    path, _ = token_paths(hermes_home)
    return FileTokenStorage(path, cfg.mcp_url).read().tokens is not None


def _dreaming_review_status(cfg, tools: list[str], reachable: bool = True) -> tuple[bool, str | None]:
    """(on, reason). reason is None when on, else the one of three causes the owner ruled on."""
    from .config import REVIEW_TOOLS

    if not cfg.dreaming_review:
        return False, "setting off"
    if not cfg.is_hosted:
        return False, "not lumberroom.cloud"
    # An empty list from an engine that never answered says nothing about the grant.
    if not reachable:
        return False, "unknown until the engine answers"
    if not all(tool in tools for tool in REVIEW_TOOLS):
        return False, "the server's grant lacks the tools"
    return True, None


def run_live_check(cfg, hermes_home: str, *, out: Callable[[str], None] = print, as_json: bool = False,
                   client_factory: Callable[..., Any] | None = None) -> int:
    """One tools/list round trip, printed as key/value lines or one JSON object.

    Shared between `status` and the wizard's own live-check step, so the two never drift.
    """
    from . import auth as auth_mod
    from .bridge import Bridge
    from .config import builtin_flags

    from hermes_cli.config import load_config

    memory_enabled, user_profile_enabled = builtin_flags(load_config())
    built_in = "off" if not memory_enabled and not user_profile_enabled else "on"

    credential = (auth_mod.token_present(cfg) if cfg.auth == "token"
                 else _oauth_credential_present(cfg, hermes_home))

    payload: dict[str, Any] = {
        "base_url": cfg.base_url, "auth": cfg.auth,
        "credential": credential, "built_in_store": built_in, "server_version": None,
        "reachable": False, "tools": [], "round_trip_ms": None, "error": None,
    }

    try:
        handle = auth_mod.build_auth(cfg, hermes_home=hermes_home, interactive=False)
    except auth_mod.AuthConfigError as exc:
        payload["error"] = str(exc)
        handle = None

    if handle is not None:
        bridge = Bridge(cfg, handle, session_id="cli-status", client_factory=client_factory)
        bridge.start()
        try:
            started = time.monotonic()
            result, listing = bridge.list_tools(timeout=5.0)
            payload["round_trip_ms"] = round((time.monotonic() - started) * 1000, 1)
            if result.ok and listing is not None:
                payload["reachable"] = True
                payload["tools"] = [t["name"] for t in listing.tools]
                payload["server_version"] = (result.structured or {}).get("serverInfo", {}).get("version")
            else:
                payload["error"] = result.error
        finally:
            bridge.close()

    dreaming_on, dreaming_reason = _dreaming_review_status(cfg, payload["tools"], payload.get("reachable", False))
    payload["dreaming_review"] = dreaming_on
    payload["dreaming_review_reason"] = dreaming_reason

    if as_json:
        out(json.dumps(payload))
    else:
        out(f"base_url: {payload['base_url']}")
        out(f"auth: {payload['auth']}")
        out(f"credential: {'present' if payload['credential'] else 'missing'}")
        out(f"built-in store: {payload['built_in_store']}")
        out(f"server version: {payload['server_version'] or 'unknown'}")
        out(f"reachable: {payload['reachable']}")
        if not payload["reachable"] and payload["error"]:
            out(f"reason: {payload['error']}")
        out(f"tools: {', '.join(payload['tools'])}")
        out(f"round trip ms: {payload['round_trip_ms']}")
        out(f"dreaming review: {'on' if dreaming_on else f'off ({dreaming_reason})'}")
    return 0 if payload["reachable"] else 1


def _status(args: Any) -> int:
    cfg, err = _load_cfg()
    if err is not None:
        print(str(err))
        return 1
    return run_live_check(cfg, _hermes_home(), out=print, as_json=getattr(args, "json", False))


def _import_builtin(args: Any) -> int:
    cfg, err = _load_cfg()
    if err is not None:
        print(str(err))
        return 1

    import httpx2

    from . import auth as auth_mod
    from .bridge import Bridge
    from .importer import MissingGrant, import_builtin as run_import, read_builtin_entries
    from .tokens import FenceTimeout

    hermes_home = _hermes_home()
    dry_run = getattr(args, "dry_run", False)
    entries = read_builtin_entries(hermes_home)

    if dry_run:
        for entry in entries:
            print(f"{entry.namespace}: {entry.text}")
        print(f"{len(entries)} entries would be imported. Nothing was posted.")
        return 0

    try:
        handle = auth_mod.build_auth(cfg, hermes_home=hermes_home, interactive=False)
    except auth_mod.AuthConfigError as exc:
        print(str(exc))
        return 1

    bridge = Bridge(cfg, handle, session_id="cli-import")
    bridge.start()
    try:
        report = run_import(bridge, hermes_home, profile=_profile_name(hermes_home), dry_run=False)
    except MissingGrant:
        print("lumberroom refused the import (403): this credential lacks mayIngest. Add "
              '"mayIngest": true to its AUTH_TOKENS entry, or use a full consent grant.')
        return 2
    except (RuntimeError, TimeoutError, auth_mod.LoginRequired, auth_mod.RefreshUnavailable,
            auth_mod.TokenSaveFailed, FenceTimeout, httpx2.TransportError) as exc:
        print(str(exc))
        return 1
    finally:
        bridge.close()

    print(f"entries seen: {len(entries)}")
    print(f"proposals new: {report.proposals_new}")
    print(f"proposals reinforced: {report.proposals_reinforced}")
    print("review with: lumberroom ingest review")
    return 0

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

    existed = run_logout(cfg, hermes_home=_hermes_home())
    print("logged out" if existed else "already logged out")
    return 0


def run_live_check(cfg, hermes_home: str, *, out: Callable[[str], None] = print, as_json: bool = False) -> int:
    """One tools/list round trip, printed as key/value lines or one JSON object.

    Shared between `status` and the wizard's own live-check step, so the two never drift.
    """
    from . import auth as auth_mod
    from .bridge import Bridge
    from .config import builtin_flags

    from hermes_cli.config import load_config

    memory_enabled, user_profile_enabled = builtin_flags(load_config())
    built_in = "off" if not memory_enabled and not user_profile_enabled else "on"

    payload: dict[str, Any] = {
        "base_url": cfg.base_url, "auth": cfg.auth,
        "credential": auth_mod.token_present(cfg), "built_in_store": built_in,
        "reachable": False, "tools": [], "round_trip_ms": None,
    }

    try:
        handle = auth_mod.build_auth(cfg, hermes_home=hermes_home, interactive=False)
        bridge = Bridge(cfg, handle, session_id="cli-status")
        bridge.start()
        try:
            started = time.monotonic()
            result, listing = bridge.list_tools(timeout=5.0)
            payload["round_trip_ms"] = round((time.monotonic() - started) * 1000, 1)
            if result.ok and listing is not None:
                payload["reachable"] = True
                payload["tools"] = [t["name"] for t in listing.tools]
        finally:
            bridge.close()
    except auth_mod.LoginRequired:
        pass

    if as_json:
        out(json.dumps(payload))
    else:
        out(f"base_url: {payload['base_url']}")
        out(f"auth: {payload['auth']}")
        out(f"credential: {'present' if payload['credential'] else 'missing'}")
        out(f"built-in store: {payload['built_in_store']}")
        out(f"reachable: {payload['reachable']}")
        out(f"tools: {', '.join(payload['tools'])}")
        out(f"round trip ms: {payload['round_trip_ms']}")
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

    from . import auth as auth_mod
    from .bridge import Bridge
    from .importer import MissingGrant, import_builtin as run_import, read_builtin_entries

    hermes_home = _hermes_home()
    dry_run = getattr(args, "dry_run", False)
    entries = read_builtin_entries(hermes_home)

    if dry_run:
        for entry in entries:
            print(f"{entry.namespace}: {entry.text}")
        print(f"{len(entries)} entries would be imported. Nothing was posted.")
        return 0

    handle = auth_mod.build_auth(cfg, hermes_home=hermes_home, interactive=False)
    bridge = Bridge(cfg, handle, session_id="cli-import")
    bridge.start()
    try:
        report = run_import(bridge, hermes_home, profile=_profile_name(hermes_home), dry_run=False)
    except MissingGrant:
        print("lumberroom refused the import (403): this credential lacks mayIngest. Add "
              '"mayIngest": true to its AUTH_TOKENS entry, or use a full consent grant.')
        return 2
    finally:
        bridge.close()

    print(f"entries seen: {len(entries)}")
    print(f"proposals new: {report.proposals_new}")
    print(f"proposals reinforced: {report.proposals_reinforced}")
    print("review with: lumberroom ingest review")
    return 0

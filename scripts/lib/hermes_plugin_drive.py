"""Drive the lumberroom provider through Hermes's own loader and MemoryManager.

Used by scripts/hermes-plugin-test.sh inside the plugin venv, with HERMES_HOME set to a throwaway
profile. No model is involved: these are the calls a Hermes turn makes.
"""

import argparse
import inspect
import json
import os
import sys
from pathlib import Path


def fact(nonce: str) -> str:
    return f"The Hermes plugin gate nickname is HERMESLARK-{nonce}."


def manager(session: str, **kwargs):
    from agent.memory_manager import MemoryManager
    from agent.secret_scope import build_profile_secret_scope, set_secret_scope
    from plugins.memory import load_memory_provider

    home = os.environ["HERMES_HOME"]
    # A real Hermes process installs the profile's .env as the secret scope before any hook runs.
    # profile_home arrived after v2026.9.21, and the gate runs against both releases.
    scope = build_profile_secret_scope(Path(home))
    if "profile_home" in inspect.signature(set_secret_scope).parameters:
        set_secret_scope(scope, profile_home=home)
    else:
        set_secret_scope(scope)
    provider = load_memory_provider("lumberroom")
    if provider is None:
        sys.exit("load_memory_provider('lumberroom') returned None")
    if not provider.is_available():
        sys.exit(f"provider unavailable: {provider.unavailable_reason()}")
    mm = MemoryManager()
    mm.add_provider(provider)
    mm.initialize_all(session_id=session, **{"platform": "cli", **kwargs})
    return mm


def write(a) -> int:
    mm = manager(a.session)
    try:
        if "memory_write" not in mm.get_all_tool_names():
            sys.exit(f"memory_write is not routed: {sorted(mm.get_all_tool_names())}")
        out = json.loads(mm.handle_tool_call("memory_write", {"content": fact(a.nonce), "namespace": "user:me"}))
        if "error" in out:
            sys.exit(f"write refused: {out['error']}")
        print(json.dumps({"id": out.get("id")}))
        return 0
    finally:
        mm.shutdown_all()


def recall(a) -> int:
    from agent.memory_manager import build_memory_context_block

    mm = manager(a.session)
    try:
        raw = mm.prefetch_all(f"What is the Hermes plugin gate nickname HERMESLARK-{a.nonce}?", session_id=a.session)
        block = build_memory_context_block(raw)
        print(block)
        return 0 if f"HERMESLARK-{a.nonce}" in block else 1
    finally:
        mm.shutdown_all()


def gated(a) -> int:
    mm = manager(a.session, platform="telegram", user_id="999000", chat_type="dm")
    try:
        if mm.get_all_tool_schemas():
            sys.exit("a gated session exposed tools")
        if mm.prefetch_all(f"HERMESLARK-{a.nonce}", session_id=a.session):
            sys.exit("a gated session recalled memory")
        out = json.loads(mm.handle_tool_call("memory_write", {"content": "x", "namespace": "user:me"}))
        if "owner" not in out.get("error", ""):
            sys.exit(f"a gated write was not refused: {out}")
        print("gated ok")
        return 0
    finally:
        mm.shutdown_all()


def entry_point(_a) -> int:
    from plugins.memory import find_provider_dir, find_provider_entry_point, load_memory_provider

    if find_provider_entry_point("lumberroom") is None:
        sys.exit("no hermes_agent.memory_providers entry point named lumberroom")
    directory = find_provider_dir("lumberroom")
    provider = load_memory_provider("lumberroom")
    if provider is None or directory is None or not (directory / "cli.py").exists():
        sys.exit(f"entry point load failed: provider={provider} dir={directory}")
    print(f"entry point ok: {directory}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["write", "recall", "gated", "entry-point"])
    ap.add_argument("--nonce", default="")
    ap.add_argument("--session", default="hpt")
    a = ap.parse_args()
    return {"write": write, "recall": recall, "gated": gated, "entry-point": entry_point}[a.command](a)


if __name__ == "__main__":
    raise SystemExit(main())

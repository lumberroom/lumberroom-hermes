"""hermes memory setup lumberroom: pick the deployment, the credential, and turn the built-in store off."""

from __future__ import annotations

from typing import Any, Callable, Mapping


def get_config_schema() -> list[dict[str, Any]]:
    raise NotImplementedError("T5")


def apply_builtin_off(config: dict[str, Any]) -> list[str]:
    """Set memory.provider, memory_enabled, user_profile_enabled, nudge_interval. Returns the lines changed."""
    raise NotImplementedError("T5")


def save_values(values: Mapping[str, Any], hermes_home: str) -> None:
    raise NotImplementedError("T5")


def post_setup(hermes_home: str, config: dict[str, Any], *,
               ask: Callable[[str], str] = input,
               ask_secret: Callable[[str], str] | None = None,
               out: Callable[[str], None] = print) -> None:
    raise NotImplementedError("T5")

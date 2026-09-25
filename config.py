"""The memory.lumberroom block of a Hermes profile's config.yaml, parsed once and validated."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

HOSTED_BASE_URL = "https://mcp.lumberroom.cloud"
TOKEN_ENV = "LUMBERROOM_HERMES_TOKEN"
DEFAULT_TOOLS: tuple[str, ...] = ("memory_search", "memory_write", "registry_get", "memory_forget")
DEFAULT_LOCAL_PLATFORMS: tuple[str, ...] = ("cli", "tui", "desktop", "acp", "cron")
AuthMode = Literal["token", "oauth"]

_INT_RANGES = {
    "digest_max_chars": (0, 50_000),
    "recall_limit": (1, 20),
    "recall_max_chars": (0, 20_000),
    "review_interval": (0, 1_000),
    "oauth_callback_port": (1024, 65535),
}
# prefetch stays under Hermes's 8.0s join, or the host skips the provider on later turns.
_FLOAT_RANGES = {
    "prefetch_timeout_s": (0.5, 7.5),
    "tool_timeout_s": (1.0, 120.0),
    "connect_timeout_s": (0.5, 30.0),
}
_BOOLS = ("recall", "digest")
_LISTS = ("tools", "owner_user_ids", "local_platforms")
_KNOWN = {"base_url", "auth", "project", *_INT_RANGES, *_FLOAT_RANGES, *_BOOLS, *_LISTS}


class ConfigError(ValueError):
    """Names the offending key: the host shows this text and nothing else."""


@dataclass(frozen=True)
class LumberroomConfig:
    base_url: str
    auth: AuthMode
    project: str = "auto"
    recall: bool = True
    digest: bool = True
    digest_max_chars: int = 6000
    recall_limit: int = 4
    recall_max_chars: int = 1200
    review_interval: int = 10
    tools: tuple[str, ...] = DEFAULT_TOOLS
    owner_user_ids: tuple[str, ...] = ()
    local_platforms: tuple[str, ...] = DEFAULT_LOCAL_PLATFORMS
    prefetch_timeout_s: float = 3.0
    tool_timeout_s: float = 20.0
    connect_timeout_s: float = 3.0
    oauth_callback_port: int = 47631

    @property
    def origin(self) -> str:
        return self.base_url[: -len("/mcp")] if self.base_url.endswith("/mcp") else self.base_url

    @property
    def mcp_url(self) -> str:
        return self.origin + "/mcp"


def section(config: Mapping[str, Any]) -> dict[str, Any]:
    memory = config.get("memory") if isinstance(config, Mapping) else None
    block = memory.get("lumberroom") if isinstance(memory, Mapping) else None
    return dict(block) if isinstance(block, Mapping) else {}


def _base_url(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("memory.lumberroom.base_url is required: the engine's URL, such as http://127.0.0.1:8787")
    url = raw.strip().rstrip("/")
    scheme, sep, rest = url.partition("://")
    if not sep or scheme.lower() not in ("http", "https") or not rest:
        raise ConfigError(f"memory.lumberroom.base_url must start with http:// or https://, got {raw!r}")
    return scheme.lower() + "://" + rest


def parse(block: Mapping[str, Any]) -> LumberroomConfig:
    unknown = sorted(set(block) - _KNOWN)
    if unknown:
        raise ConfigError(f"unknown key memory.lumberroom.{unknown[0]}")
    auth = block.get("auth")
    if auth not in ("token", "oauth"):
        raise ConfigError("memory.lumberroom.auth must be token or oauth")
    values: dict[str, Any] = {"base_url": _base_url(block.get("base_url")), "auth": auth}
    if "project" in block:
        project = block["project"]
        if not isinstance(project, str) or not project.strip():
            raise ConfigError("memory.lumberroom.project must be auto, none, or a slug or path")
        values["project"] = project.strip()
    for key in _BOOLS:
        if key in block:
            if not isinstance(block[key], bool):
                raise ConfigError(f"memory.lumberroom.{key} must be true or false")
            values[key] = block[key]
    for key, (lo, hi) in _INT_RANGES.items():
        if key in block:
            v = block[key]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ConfigError(f"memory.lumberroom.{key} must be a whole number from {lo} to {hi}")
            values[key] = v
    for key, (lo, hi) in _FLOAT_RANGES.items():
        if key in block:
            v = block[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
                raise ConfigError(f"memory.lumberroom.{key} must be a number from {lo} to {hi}")
            values[key] = float(v)
    for key in _LISTS:
        if key in block:
            v = block[key]
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                raise ConfigError(f"memory.lumberroom.{key} must be a list of strings")
            values[key] = tuple(dict.fromkeys(x.strip() for x in v))
    if "tools" in values and not values["tools"]:
        raise ConfigError("memory.lumberroom.tools must name at least one tool")
    for owner in values.get("owner_user_ids", ()):
        platform, sep, uid = owner.partition(":")
        if not sep or not platform or not uid:
            raise ConfigError(f"memory.lumberroom.owner_user_ids entries look like telegram:123456789, got {owner!r}")
        # The api_server platform takes a turn's author from the request body, and Hermes documents
        # it as a label that grants nothing (gateway/platforms/api_server.py:669-677,4028). Listing
        # one would hand the owner's memory to anyone holding the API server key.
        if platform == "api_server":
            raise ConfigError(
                f"memory.lumberroom.owner_user_ids cannot list {owner!r}: an api_server author is "
                "whatever the caller puts in the request body"
            )
    return LumberroomConfig(**values)


def load() -> LumberroomConfig:
    """Parse the active profile's block. Hermes resolves the profile from its context."""
    from hermes_cli.config import load_config

    return parse(section(load_config()))


def builtin_flags(config: Mapping[str, Any]) -> tuple[bool, bool]:
    """(memory_enabled, user_profile_enabled), both defaulting to on as Hermes does."""
    memory = config.get("memory") if isinstance(config, Mapping) else None
    memory = memory if isinstance(memory, Mapping) else {}
    return (memory.get("memory_enabled", True) is not False, memory.get("user_profile_enabled", True) is not False)


def write_section(config: dict[str, Any], values: Mapping[str, Any]) -> None:
    memory = config.setdefault("memory", {})
    block = memory.setdefault("lumberroom", {})
    block.update(values)

"""The gate's driver script runs against more than one Hermes release."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

DRIVE = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "hermes_plugin_drive.py"


def _drive():
    spec = importlib.util.spec_from_file_location("hermes_plugin_drive", DRIVE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def no_provider(monkeypatch, hermes_home):
    # Stops manager() right after the secret scope goes in, before any network.
    monkeypatch.setattr("plugins.memory.load_memory_provider", lambda name: None)
    monkeypatch.setattr("agent.secret_scope.build_profile_secret_scope", lambda home: {"K": "v"})
    return hermes_home


def test_the_driver_installs_the_scope_on_a_hermes_without_profile_home(monkeypatch, no_provider):
    # v2026.9.21 signature: set_secret_scope(secrets).
    seen = []
    monkeypatch.setattr("agent.secret_scope.set_secret_scope", lambda secrets: seen.append(secrets))
    with pytest.raises(SystemExit, match="returned None"):
        _drive().manager("s")
    assert seen == [{"K": "v"}]


def test_the_driver_stamps_profile_home_where_hermes_takes_it(monkeypatch, no_provider):
    seen = []
    monkeypatch.setattr("agent.secret_scope.set_secret_scope",
                        lambda secrets, *, profile_home=None: seen.append((secrets, profile_home)))
    with pytest.raises(SystemExit, match="returned None"):
        _drive().manager("s")
    assert seen == [({"K": "v"}, str(no_provider))]

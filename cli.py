"""hermes lumberroom login | logout | status | import-builtin.

Hermes imports this file during argparse setup, before any provider loads, so heavy imports wait
inside the handlers.
"""

from __future__ import annotations

from typing import Any


def register_cli(subparser: Any) -> None:
    raise NotImplementedError("T5")


def lumberroom_command(args: Any) -> int:
    raise NotImplementedError("T5")

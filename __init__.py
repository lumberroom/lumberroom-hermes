"""lumberroom as Hermes Agent's memory provider.

Hermes picks this directory out by the text register_memory_provider in this file, before it
imports anything, so the call below has to stay spelled out here.
"""

__version__ = "0.1.0"


def register(ctx) -> None:
    # Imported here so `import lumberroom_hermes` stays cheap for the entry-point scan.
    from .provider import LumberroomProvider

    ctx.register_memory_provider(LumberroomProvider())

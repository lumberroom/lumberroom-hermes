"""lumberroom as Hermes Agent's memory provider.

Hermes picks this directory out by the text register_memory_provider in this file, before it
imports anything, so the call below has to stay spelled out here.
"""


def __getattr__(name: str):
    # Lazy, so importing this file carries no relative import: pytest imports the repository root's
    # __init__.py on its own, with no parent package, and a module-level one fails there.
    if name == "__version__":
        from ._version import __version__
        return __version__
    raise AttributeError(name)


def register(ctx) -> None:
    # Imported here so `import lumberroom_hermes` stays cheap for the entry-point scan.
    from .provider import LumberroomProvider

    ctx.register_memory_provider(LumberroomProvider())

    # The memory-provider loader hands over a context without register_skill, so the skill is
    # registered only where the general plugin loader is the caller. The text is a copy of the
    # engine's skills/lr-review/SKILL.md, kept identical by the engine's scripts/sync-skills.sh.
    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is not None:
        from pathlib import Path

        register_skill(
            "lr-review",
            Path(__file__).parent / "skills" / "lr-review" / "SKILL.md",
            "Work the lumberroom review queue: dreaming proposals, conflicts, duplicates, stale facts.",
        )

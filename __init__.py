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

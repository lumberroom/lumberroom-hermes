"""The plugin's version, in a module with no imports.

Hermes loads cli.py under a synthetic parent package without running __init__.py, so a name
defined there is missing on that path. bridge.py and auth.py read the version from here instead.
"""

__version__ = "1.0.0"

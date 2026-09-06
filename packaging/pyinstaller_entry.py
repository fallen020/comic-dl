"""Frozen-executable entry point for PyInstaller (see packaging/comic-dl.spec).

The spec must not point at ``src/comic_dl/__main__.py`` directly: PyInstaller
compiles its entry script as a top-level ``__main__`` module, where the
relative ``from .cli import main`` in that file raises ``ImportError``. Going
through the package first gives the real ``comic_dl.__main__`` module a
parent package to resolve against.
"""

from comic_dl.__main__ import entry

if __name__ == "__main__":
    raise SystemExit(entry())

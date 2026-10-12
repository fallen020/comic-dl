#!/usr/bin/env python3
"""Bake static shell-completion scripts from the argparse builders.

shtab walks a single parser tree, but dispatch here is manual
two-stage (`main()` matches the command itself), so this script
assembles a synthetic root parser used only for generation. The
per-command builders stay the single source of flag definitions;
nothing here may declare a flag.

Run without arguments to rewrite the scripts in place; ``--check``
instead verifies they are current (used by the completion tests so
CI fails on drift). Requires the ``shtab`` dev extra.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the package importable when run from a bare checkout (CI, pre-commit).
# Requires the import just below to carry the deliberate E402 exception.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

try:
    import shtab
except ImportError:
    sys.exit("error: the 'shtab' dev extra is required (uv sync --extra dev)")

from comic_dl.cli import (  # noqa: E402
    _LIBRARY_COMMANDS,
    _build_cache_parser,
    _build_config_parser,
    _build_cookie_parser,
    _build_first_stage_parser,
    _build_list_sources_parser,
    _build_self_parser,
    _build_self_site_parser,
    _build_update_parser,
)
from comic_dl.cli.library import _build_parser as _build_library_parser  # noqa: E402
from comic_dl.ui import ComicArgumentParser  # noqa: E402

_SHELLS = ("bash", "zsh", "fish", "powershell")
_SUFFIX = {"bash": "bash", "zsh": "zsh", "fish": "fish", "powershell": "ps1"}
_DEST = Path(__file__).resolve().parent.parent / "src" / "comic_dl" / "cli" / "completions"


def _short_help(parser: argparse.ArgumentParser, fallback: str) -> str:
    """First line of a builder's description (shtab needs ``help=``)."""
    if parser.description:
        return parser.description.splitlines()[0]
    return fallback


def _attach(
    subparsers: argparse._SubParsersAction,
    name: str,
    parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Add ``name`` reusing ``parser``'s flags (``help=`` is load-bearing:
    CPython records no subcommand entry without it, and shtab discovers
    subcommands exclusively through those entries)."""
    return subparsers.add_parser(
        name, parents=[parser], add_help=False, help=_short_help(parser, name)
    )


def build_root_parser() -> ComicArgumentParser:
    """Synthetic ``comic-dl`` parser for shtab generation only.

    Never used for dispatch: the first-stage ``positional_url`` is
    dropped (download-only), globals stay on the root, and detached
    trees (``self site`` grandchildren, ``plugin`` children) are
    grafted so three-level commands complete.
    """
    first = _build_first_stage_parser()
    first._actions = [a for a in first._actions if a.dest != "positional_url"]
    root = ComicArgumentParser(prog="comic-dl", parents=[first], add_help=False)
    sub = root.add_subparsers(dest="command")

    _attach(sub, "update", _build_update_parser())
    # The live dispatcher prefers the download `update` over the
    # shadowing library `update`; its flags ride along here so no
    # library word loses completion.
    update_node = sub.choices["update"]
    for action in _build_library_parser("update")._actions:
        if action.dest != "help":
            update_node._add_action(action)

    _attach(sub, "self", _build_self_parser())
    # `self site` grandchildren live in a detached builder (manual
    # dispatch: _run_self -> _run_self_site), so graft them onto the
    # synthetic `site` node.
    site_node = next(
        a for a in sub.choices["self"]._actions if isinstance(a, argparse._SubParsersAction)
    ).choices["site"]
    site_sub = site_node.add_subparsers(dest="action")
    site_builder = _build_self_site_parser()
    site_builder_sub = next(
        a for a in site_builder._actions if isinstance(a, argparse._SubParsersAction)
    )
    for name, child in site_builder_sub.choices.items():
        if isinstance(child, argparse.ArgumentParser):
            _attach(site_sub, name, child)

    _attach(sub, "cookie", _build_cookie_parser())
    _attach(sub, "cache", _build_cache_parser())
    _attach(sub, "config", _build_config_parser())
    _attach(sub, "list-sources", _build_list_sources_parser())
    for cmd in sorted(_LIBRARY_COMMANDS):
        if cmd == "update":
            continue
        _attach(sub, cmd, _build_library_parser(cmd))

    # `plugin` parsers are built inline in plugins.py `_cmd_*` (not
    # reusable builders) and the old scripts completed names only:
    # stubs preserve parity without refactoring runtime code.
    plugin = sub.add_parser("plugin", add_help=False, help="Inspect and scaffold site plugins")
    plugin_sub = plugin.add_subparsers(dest="plugin_cmd")
    for name in ("list", "validate", "scaffold"):
        plugin_sub.add_parser(name, add_help=False, help=name)
    sub.add_parser("help", add_help=False, help="Show help for a command")
    completion = sub.add_parser(
        "completion", add_help=False, help="Print a shell completion script"
    )
    completion.add_argument("shell", nargs="?", choices=["bash", "zsh", "fish", "powershell"])

    # Path completion for file-typed globals (fish `complete -f` and
    # bash `-o default` would otherwise regress to file-or-nothing).
    # `.complete` is shtab-only metadata; argparse never reads it, so
    # sharing the builders' action objects is side-effect free.
    for action in root._actions:
        if not action.option_strings:
            continue
        opts = set(action.option_strings)
        if {"--output", "-o"} & opts:
            action.complete = shtab.DIRECTORY  # type: ignore[attr-defined]
        elif {"--file", "-f", "--config", "--debug-file"} & opts:
            action.complete = shtab.FILE  # type: ignore[attr-defined]
    return root


def _generate() -> dict[str, str]:
    root = build_root_parser()
    return {shell: shtab.complete(root, shell=shell) for shell in _SHELLS}


def main(argv: list[str]) -> int:
    """Rewrite the baked scripts, or verify them with ``--check``."""
    check = argv == ["--check"]
    if argv and not check:
        print(f"usage: {Path(sys.argv[0]).name} [--check]", file=sys.stderr)
        return 2
    generated = _generate()
    failed = False
    for shell, text in generated.items():
        path = _DEST / f"comic-dl.{_SUFFIX[shell]}"
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                print(f"stale: {path} (run scripts/gen-completions.py)")
                failed = True
        else:
            _DEST.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

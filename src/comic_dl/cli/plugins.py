"""plugin subcommand: list, validate, and scaffold third-party sources."""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape as esc

from ..errors import EXIT_ERROR, EXIT_OK, EXIT_USAGE
from ..scrapers.registry import list_sources, load_plugins, plugin_load_errors
from ..ui import (
    JSON_SCHEMA_VERSION,
    console,
    print_dim,
    print_error,
    print_success,
    print_warning,
)

VALID_CAPABILITIES = frozenset({"chapter", "series"})


@dataclass(slots=True)
class _Finding:
    source: str
    problems: list[str]


def run_plugin_command(cmd: str, argv: list[str]) -> int:
    """Parse and run a plugin subcommand. Returns the process exit code."""
    if cmd == "list":
        return _cmd_list(argv)
    if cmd == "validate":
        return _cmd_validate(argv)
    if cmd == "scaffold":
        return _cmd_scaffold(argv)
    return EXIT_USAGE


def _cmd_list(argv: list[str]) -> int:
    """Show installed third-party sources, including ones that failed to load."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="comic-dl plugin list",
        description="List third-party (plugin) sources.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON for scripting")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    load_plugins()
    errors = plugin_load_errors()
    entries = [e for e in list_sources() if not e.builtin]

    if args.json:
        payload = [
            {
                "domain": e.domain,
                "name": e.name,
                "version": e.version,
                "capabilities": sorted(e.capabilities),
                "priority": e.priority,
                "broken": None,
            }
            for e in entries
        ]
        payload.extend(
            {
                "domain": "<unloadable>",
                "name": name,
                "version": "",
                "capabilities": [],
                "priority": 0,
                "broken": reason,
            }
            for name, reason in sorted(errors.items())
        )
        console.print(
            json.dumps(
                {"schema_version": JSON_SCHEMA_VERSION, "plugins": payload},
                indent=2,
            ),
            soft_wrap=True,
        )
        return EXIT_OK

    if not entries and not errors:
        print_dim("No third-party sources installed.")
        print_dim("See docs/usage/plugins.md to install or write one.")
        return EXIT_OK

    for e in entries:
        caps = ", ".join(sorted(e.capabilities)) or "\u2014"
        console.print(esc(f"  {e.domain}  {e.name} {e.version}  [{caps}]"))
    for name, reason in sorted(errors.items()):
        print_warning(f"{name}: failed to load ({reason})")
        print_dim("  Re-install the plugin or fix the error above, then restart.")
    return EXIT_ERROR if errors else EXIT_OK


def _cmd_validate(argv: list[str]) -> int:
    """Shape-check a Source class in a file or directory without running it."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="comic-dl plugin validate",
        description="Validate a plugin's Source class shape.",
    )
    parser.add_argument(
        "path",
        help="path to a .py file or a directory containing a source.py",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    path = Path(args.path)
    if path.is_dir():
        path = path / "source.py"
    if not path.is_file():
        print_error(f"Not a source file: {path}")
        return EXIT_USAGE

    module = _import_source_module(path)
    if module is None:
        return EXIT_ERROR

    findings = [_check_class(cls) for cls in _source_classes(module)]
    if not findings:
        print_warning(f"No Source classes found in {path} (no 'domain' attribute on any class).")
        return EXIT_ERROR

    bad = False
    for finding in findings:
        if not finding.problems:
            print_success(f"{finding.source}")
            continue
        bad = True
        print_error(f"{finding.source}")
        for problem in finding.problems:
            print_dim(f"  · {problem}")
    return EXIT_ERROR if bad else EXIT_OK


def _import_source_module(path: Path) -> types.ModuleType | None:
    """Import ``path`` as a module by file path (sibling imports preserved)."""
    sys.path.insert(0, str(path.parent))
    name = path.stem or "plugin_source"
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot build module spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        print_error(f"Import failed: {type(exc).__name__}: {exc}")
        return None
    finally:
        sys.modules.pop(name, None)
        sys.path.pop(0)


def _source_classes(module: types.ModuleType) -> list[type]:
    """Classes in ``module`` that look like Source classes (have ``domain``)."""
    return [
        cls
        for _name, cls in inspect.getmembers(module, inspect.isclass)
        if cls.__module__ == module.__name__ and hasattr(cls, "domain")
    ]


def _check_class(cls: type) -> _Finding:
    """Shape-check one Source class against the plugin contract."""
    source = f"{cls.__module__}.{cls.__name__}"
    problems: list[str] = []

    domain = getattr(cls, "domain", "")
    if not isinstance(domain, str) or not domain.strip():
        problems.append("domain: expected a non-empty string (e.g. 'mysite.example')")
    elif "." not in domain:
        problems.append(f"domain: {domain!r} is not a hostname")

    caps = getattr(cls, "capabilities", {"chapter"})
    if not isinstance(caps, (set, frozenset, list, tuple)):
        problems.append(f"capabilities: expected a set, got {type(caps).__name__}")
    else:
        unknown = sorted(set(caps) - VALID_CAPABILITIES)
        if unknown:
            problems.append(
                f"capabilities: unknown {unknown}; expected one of {sorted(VALID_CAPABILITIES)}"
            )
        caps = set(caps)

    name = getattr(cls, "name", None)
    if name is not None and not isinstance(name, str):
        problems.append(f"name: expected a string, got {type(name).__name__}")
    version = getattr(cls, "version", None)
    if version is not None and not isinstance(version, str):
        problems.append(f"version: expected a string, got {type(version).__name__}")

    priority = getattr(cls, "priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int):
        problems.append(f"priority: expected an int, got {type(priority).__name__}")

    matches_url = getattr(cls, "matches_url", None)
    if matches_url is not None and not callable(matches_url):
        problems.append("matches_url: expected a method (url) -> bool")
    matches_series_url = getattr(cls, "matches_series_url", None)
    if matches_series_url is not None and not callable(matches_series_url):
        problems.append("matches_series_url: expected a method (url) -> bool")

    if "chapter" in caps and not callable(getattr(cls, "scrape", None)):
        problems.append(
            "scrape: missing — required for the 'chapter' capability "
            "(async scrape(url, client) -> PostMetadata)"
        )
    if "series" in caps and not callable(getattr(cls, "scrape_series", None)):
        problems.append(
            "scrape_series: missing — required for the 'series' capability "
            "(async scrape_series(url, client) -> SeriesMetadata)"
        )
    return _Finding(source=source, problems=problems)


_SCAFFOLD_PYPROJECT = """\
[project]
name = "{pkg}"
version = "0.1.0"
description = "comic-dl scraper plugin for {domain}."
requires-python = ">=3.11"
dependencies = ["curl-cffi", "comic-dl"]

[project.entry-points."comic_dl.sources"]
{entry_name} = "comic_dl_{pkg}.source:{class_name}"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["comic_dl_{pkg}"]
"""

_SCAFFOLD_SOURCE = '''\
"""Scraper plugin for {domain} (chapter-only).

Fill in the selectors below, then validate the shape with:
    comic-dl plugin validate .
"""

from __future__ import annotations

from curl_cffi.requests import AsyncSession

from comic_dl.models import ImageItem, PostMetadata


class {class_name}:
    domain = "{domain}"
    name = "{pkg}"
    version = "0.1.0"
    capabilities = {{"chapter"}}
    priority = 0

    def matches_url(self, url: str) -> bool:
        return url.startswith("https://{domain}/")

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        # soup = await BaseScraper.fetch_html(url, client)
        # ... parse the page and collect image URLs ...
        return PostMetadata(
            series_title="Series",
            chapter_title="Chapter",
            images=[
                ImageItem(
                    url="https://{domain}/images/1.jpg",
                    page_number=1,
                    filename="001.jpg",
                )
            ],
        )
'''


def _cmd_scaffold(argv: list[str]) -> int:
    """Create a plugin package skeleton (pyproject.toml + source.py)."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="comic-dl plugin scaffold",
        description="Scaffold a new scraper plugin package in the current directory.",
    )
    parser.add_argument(
        "name",
        help="plugin package name (e.g. mysite)",
    )
    parser.add_argument(
        "--domain",
        default="example.com",
        help="site host the plugin scrapes (default: example.com)",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    pkg = args.name
    if not pkg.isidentifier():
        print_error(f"Invalid plugin name {pkg!r}; expected an identifier.")
        return EXIT_USAGE
    if not (args.domain and "." in args.domain and "/" not in args.domain):
        print_error(f"Invalid plugin domain {args.domain!r}.")
        return EXIT_USAGE

    root = Path.cwd() / f"comic_dl_{pkg}"
    pkg_dir = root / f"comic_dl_{pkg}"
    if root.exists():
        print_error(f"Directory already exists: {root}")
        return EXIT_USAGE
    try:
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").touch()
        class_name = "".join(part.title() for part in pkg.split("_")) + "Source"
        (root / "pyproject.toml").write_text(
            _SCAFFOLD_PYPROJECT.format(
                pkg=pkg,
                domain=args.domain,
                entry_name=pkg.replace("_", "-"),
                class_name=class_name,
            ),
            encoding="utf-8",
        )
        (pkg_dir / "source.py").write_text(
            _SCAFFOLD_SOURCE.format(pkg=pkg, domain=args.domain, class_name=class_name),
            encoding="utf-8",
        )
    except OSError as exc:
        print_error(f"Could not write scaffold: {exc}")
        return EXIT_ERROR

    print_success(f"Scaffolded plugin at {root}")
    print_dim(f"Validate it with: comic-dl plugin validate {root / pkg_dir}")
    print_dim(f"Install with: uv pip install -e {root}")
    return EXIT_OK

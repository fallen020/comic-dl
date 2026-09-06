"""Library subcommands (list, info, latest, remove, restore, update)."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.markup import escape as esc
from rich.prompt import Prompt

from ..config import configured_output_dir
from ..errors import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK, EXIT_USAGE
from ..library import Library, library_path
from ..scrapers.registry import list_sources, url_in_domain
from ..ui import (
    ERROR,
    JSON_SCHEMA_VERSION,
    MUTED,
    ComicArgumentParser,
    console,
    err_console,
    format_bytes,
    glyphs,
    print_dim,
    print_error,
    print_header,
    print_meta,
    print_success,
    print_table,
    print_warning,
    suggest,
)
from ..utils import normalize_url, sanitize_filename

TRASH_TTL_DAYS = 7

RESTORE_SIDECAR_SUFFIX = ".restore.json"

COMMANDS = frozenset({"list", "info", "latest", "remove", "restore", "update"})


def _validate_output_dir(output_dir: Path) -> str | None:
    """Return a human explanation when ``output_dir`` can't be a library root.

    Covers three distinct failures, all reported as usage errors (exit 2):
    the path exists but isn't a directory, exists but isn't writable, or a
    nonexistent path whose parent chain can't be created. Returns ``None``
    (creating the directory if needed) when it can serve as a root.
    """
    try:
        if output_dir.exists():
            if not output_dir.is_dir():
                return "It exists and is not a directory."
            if not os.access(output_dir, os.W_OK):
                return "It exists but is not writable."
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        return None
    except OSError as exc:
        return f"It can't be created ({exc.strerror or exc})."


def _source_domains() -> list[str]:
    return [e.domain for e in list_sources()]


def _matches_source(s: dict, source: str) -> bool:
    """True when a series row belongs to the requested ``source`` domain."""
    domain = source.strip().lower().rstrip("/")
    site = (s.get("source_site") or "").strip().lower().rstrip("/")
    if site == domain or site.endswith("." + domain):
        return True
    return bool(url_in_domain(s.get("source") or "", domain))


def _print_source_suggestion(source: str) -> None:
    domain = source.strip().lower().rstrip("/")
    hint = suggest(domain, _source_domains())
    if hint and hint != domain:
        err_console.print(f"  [{MUTED}]Did you mean:[/] {hint}?")


def _detect_source_domain(query: str) -> str | None:
    """Return a registered source domain when ``query`` looks like one."""
    q = query.strip().lower().rstrip("/")
    if not q:
        return None
    for domain in _source_domains():
        d = domain.lower().lstrip(".")
        if q == d or q == f"www.{d}" or q.endswith(f".{d}"):
            return d
    return None


_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*"
    r"\.[a-z]{2,}$"
)


def _looks_like_domain(query: str) -> bool:
    """True when ``query`` is a bare hostname (e.g. ``gedecomix.com``)."""
    q = query.strip().lower()
    return bool(_DOMAIN_RE.fullmatch(q))


def _suggest_series_title(library: Library, query: str) -> str | None:
    if not library.available:
        return None
    titles = [s["title"] for s in library.list_series()]
    hint = suggest(query, titles)
    if hint and hint != query:
        return hint
    return None


def _build_parser(cmd: str) -> argparse.ArgumentParser:
    parser = ComicArgumentParser(
        prog=f"comic-dl {cmd}",
        description="",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Library root directory (default: per-user downloads folder)",
    )
    if cmd in ("list", "info", "latest"):
        parser.add_argument(
            "--json", action="store_true",
            help="Emit machine-readable JSON on stdout",
        )
    if cmd in ("list", "latest"):
        parser.add_argument(
            "--source", dest="source", default=None,
            help="Only show series/chapters from this source domain",
        )
    if cmd == "latest":
        parser.add_argument(
            "-n", "--days", type=int, default=7,
            help="Show chapters downloaded in the last N days (default: 7)",
        )
    if cmd in ("remove", "restore"):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would happen without changing anything",
        )
        parser.add_argument(
            "--json", action="store_true",
            help="Emit machine-readable JSON on stdout",
        )
    if cmd == "remove":
        parser.add_argument(
            "-y", "--yes", action="store_true",
            help="Skip the confirmation prompt",
        )
    if cmd in ("info", "remove", "restore"):
        parser.add_argument("series", help="Series title, series ID, or series URL")
    return parser


def run_library_command(cmd: str, argv: list[str]) -> int:
    """Parse and run a library subcommand. Returns the process exit code."""
    parser = _build_parser(cmd)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse raises for --help (0) and usage errors (2); keep the code.
        return exc.code if isinstance(exc.code, int) else 0

    if args.output is None:
        args.output = configured_output_dir()

    if reason := _validate_output_dir(args.output):
        print_error(f"Library path not found: {args.output}.")
        print_dim(f"{reason} Or omit -o to use the default ({configured_output_dir()}).")
        return EXIT_USAGE

    if cmd not in COMMANDS:
        return EXIT_USAGE

    library = Library(library_path(args.output))
    try:
        library.open()
    except OSError as exc:
        print_error(f"Cannot open library at {library_path(args.output)}.")
        print_dim(
            f"{exc.strerror or exc}. Or omit -o to use the default "
            f"({configured_output_dir()})."
        )
        return EXIT_USAGE
    try:
        _purge_trash(args.output)
        if cmd == "list":
            return _cmd_list(
                library, args.output,
                as_json=args.json, source=args.source,
            )
        if cmd == "info":
            return _cmd_info(
                library, args.output, args.series,
                as_json=args.json,
            )
        if cmd == "latest":
            return _cmd_latest(library, args, as_json=args.json)
        if cmd == "remove":
            return _cmd_remove(
                library, args.output, args.series,
                yes=args.yes, dry_run=args.dry_run, as_json=args.json,
            )
        if cmd == "restore":
            return _cmd_restore(
                library, args.output, args.series, dry_run=args.dry_run,
                as_json=args.json,
            )
        return EXIT_USAGE
    finally:
        library.close()


# ── list ───────────────────────────────────────────────────────

def _cmd_list(
    library: Library,
    output_dir: Path,
    *,
    as_json: bool,
    source: str | None = None,
) -> int:
    if not library.available:
        print_error(f"No library database at {library_path(output_dir)}.")
        print_dim("Download a series first, or point -o at the right output root.")
        return EXIT_ERROR
    series = library.list_series()
    if source is not None:
        series = [s for s in series if _matches_source(s, source)]
    if as_json:
        payload = [
            {
                "series_id": s["series_id"],
                "title": s["title"],
                "source": s.get("source") or None,
                "source_site": s.get("source_site") or None,
                "relative_path": s.get("relative_path") or None,
                "chapter_count": s["chapter_count"],
                "total_size": s["total_size"],
                "last_checked": s.get("last_checked"),
                "last_updated": s.get("last_updated"),
                "directory": str(_resolve_series_dir(output_dir, s)),
            }
            for s in series
        ]
        console.print(json.dumps(
            {"schema_version": JSON_SCHEMA_VERSION, "series": payload}, indent=2,
        ), soft_wrap=True)
        return EXIT_OK
    if not series:
        if source is not None:
            print_dim(
                f"No series from '{source}' in the library.",
                console_obj=console,
            )
            _print_source_suggestion(source)
        else:
            print_dim(
                f"Library is empty at {library_path(output_dir)}.",
                console_obj=console,
            )
            print_dim("Get started: comic-dl -u <URL>", console_obj=console)
        return EXIT_OK
    rows = [
        [
            esc(s["title"]),
            esc(s["source_site"] or ""),
            str(s["chapter_count"]),
            format_bytes(s["total_size"]),
            s["last_updated"] or glyphs().dash,
        ]
        for s in series
    ]
    print_table("Library", ["Title", "Source", "Chapters", "Size", "Last updated"], rows)
    console.print()
    print_dim(
        f"{len(series)} series in {library_path(output_dir)}",
        console_obj=console,
    )
    return EXIT_OK


# ── info ───────────────────────────────────────────────────────

def _cmd_info(
    library: Library,
    output_dir: Path,
    query: str,
    *,
    as_json: bool,
) -> int:
    if not library.available:
        print_error(f"No library database at {library_path(output_dir)}.")
        print_dim("Download a series first, or point -o at the right output root.")
        return EXIT_ERROR
    match = _resolve_series(library, query)
    if match is None:
        return EXIT_USAGE
    s = match
    series_dir = _resolve_series_dir(output_dir, s)
    chapters = library.get_chapters(s["series_id"])

    if as_json:
        _sort_chapters(chapters)
        payload = {
            "schema_version": JSON_SCHEMA_VERSION,
            "series_id": s["series_id"],
            "title": s["title"],
            "source": s.get("source") or None,
            "source_site": s.get("source_site") or None,
            "directory": str(series_dir),
            "chapter_count": len(chapters),
            "last_checked": s.get("last_checked"),
            "last_updated": s.get("last_updated"),
            "chapters": [
                {
                    "ok": bool(ch.get("cbz") and (series_dir / ch["cbz"]).exists()),
                    "chapter_no": ch.get("chapter_no"),
                    "title": ch.get("title"),
                    "cbz": ch.get("cbz"),
                    "page_count": ch.get("page_count"),
                    "size_bytes": ch.get("size_bytes"),
                    "downloaded_at": ch.get("downloaded_at"),
                }
                for ch in chapters
            ],
        }
        console.print(json.dumps(payload, indent=2), soft_wrap=True)
        return EXIT_OK

    console.print()
    print_header(f"Series: {s['title']}", console_obj=console)
    print_meta("Series ID", s["series_id"], console_obj=console)
    if s.get("source"):
        print_meta("Source", s["source"], console_obj=console)
    print_meta("Directory", str(series_dir), console_obj=console)
    print_meta("Chapters", str(len(chapters)), console_obj=console)
    print_meta("Last checked", s.get("last_checked") or glyphs().dash, console_obj=console)
    print_meta("Last updated", s.get("last_updated") or glyphs().dash, console_obj=console)

    if not chapters:
        print_dim("No chapters recorded.", console_obj=console)
        return EXIT_OK

    _sort_chapters(chapters)
    rows = []
    unverified: list[tuple[dict, str]] = []
    for ch in chapters:
        cbz = ch.get("cbz")
        if not cbz:
            mark, mark_style = glyphs().ndash, MUTED
            reason = "no cbz recorded (never downloaded)"
        elif (series_dir / cbz).exists():
            mark, mark_style = glyphs().ok, "green"
            reason = ""
        else:
            mark, mark_style = glyphs().fail, "red"
            reason = f"cbz not found at {series_dir / cbz}"
        if reason:
            unverified.append((ch, reason))
        rows.append([
            f"[{mark_style}]{mark}[/]",
            esc(ch.get("chapter_no") or ""),
            esc(ch.get("title") or ""),
            str(ch["page_count"]) if ch.get("page_count") is not None else "",
            format_bytes(ch["size_bytes"]) if ch.get("size_bytes") else "",
            ch.get("downloaded_at") or "",
        ])
    print_table(None, ["Verified", "#", "Chapter", "Pages", "Size", "Downloaded"], rows)
    if unverified:
        err_console.print()
        err_console.print(
            f"  [{MUTED}]{len(unverified)} chapter(s) not verified:[/]"
        )
        for ch, reason in unverified:
            err_console.print(
                f"    [{ERROR}]{glyphs().fail}[/] "
                f"#{ch.get('chapter_no') or '?'} {esc(ch.get('title') or '')}: "
                f"{esc(reason)}"
            )
    return EXIT_OK


# ── latest ─────────────────────────────────────────────────────

def _cmd_latest(
    library: Library,
    args: argparse.Namespace,
    *,
    as_json: bool,
) -> int:
    if not library.available:
        print_error(f"No library database at {library_path(args.output)}.")
        print_dim("Download a series first, or point -o at the right output root.")
        return EXIT_ERROR
    if args.days < 1:
        print_error("--days must be at least 1.")
        return EXIT_USAGE
    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    chapters = library.chapters_since(cutoff.isoformat(timespec="seconds"))
    if args.source is not None:
        allowed = {
            s["series_id"]
            for s in library.list_series()
            if _matches_source(s, args.source)
        }
        chapters = [c for c in chapters if c["series_id"] in allowed]
    if as_json:
        payload = [
            {
                "series_id": c["series_id"],
                "series_title": c["series_title"],
                "chapter_title": c.get("chapter_title"),
                "chapter_no": c.get("chapter_no"),
                "cbz": c.get("cbz"),
                "size_bytes": c.get("size_bytes"),
                "page_count": c.get("page_count"),
                "downloaded_at": c.get("downloaded_at"),
            }
            for c in chapters
        ]
        console.print(json.dumps(
            {"schema_version": JSON_SCHEMA_VERSION, "chapters": payload}, indent=2,
        ), soft_wrap=True)
        return EXIT_OK
    if not chapters:
        if args.source is not None:
            print_dim(
                f"No chapters from '{args.source}' downloaded in the "
                f"last {args.days} day(s).",
                console_obj=console,
            )
            _print_source_suggestion(args.source)
        else:
            print_dim(
                f"No chapters downloaded in the last {args.days} day(s).",
                console_obj=console,
            )
        return EXIT_OK
    rows = [
        [
            esc(c["series_title"]),
            esc(c["chapter_title"] or c["chapter_no"] or ""),
            format_bytes(c["size_bytes"]) if c.get("size_bytes") else "",
            c.get("downloaded_at") or "",
        ]
        for c in chapters
    ]
    print_table("Latest downloads", ["Series", "Chapter", "Size", "Downloaded"], rows)
    console.print()
    print_dim(
        f"{len(chapters)} chapter(s) in the last {args.days} day(s)",
        console_obj=console,
    )
    return EXIT_OK


# ── remove ─────────────────────────────────────────────────────

def _cmd_remove(
    library: Library,
    output_dir: Path,
    query: str,
    *,
    yes: bool,
    dry_run: bool,
    as_json: bool = False,
) -> int:
    if not library.available:
        print_error(f"No library database at {library_path(output_dir)}.")
        print_dim("Download a series first, or point -o at the right output root.")
        return EXIT_ERROR
    match = _resolve_series(library, query)
    if match is None:
        return EXIT_USAGE
    s = match
    series_dir = _resolve_series_dir(output_dir, s)

    output_resolved = output_dir.resolve()
    if not series_dir.is_relative_to(output_resolved):
        print_error("Refusing to remove: series directory escapes the output root.")
        return EXIT_ERROR
    if series_dir == output_resolved:
        print_error("Refusing to remove: series directory is the output root itself.")
        return EXIT_ERROR

    chapters = library.get_chapters(s["series_id"])
    total_size = sum(c.get("size_bytes") or 0 for c in chapters)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    entry_name = f"{sanitize_filename(s['title']) or 'series'}-{stamp}"
    trash = _trash_dir(output_dir)
    dest = trash / entry_name

    if dry_run:
        if as_json:
            console.print(json.dumps({
                "schema_version": JSON_SCHEMA_VERSION,
                "dry_run": True,
                "series_id": s["series_id"],
                "title": s["title"],
                "directory": str(series_dir),
                "chapter_count": len(chapters),
                "size_bytes": total_size,
            }, indent=2), soft_wrap=True)
        else:
            console.print()
            print_header(f"Remove series: {s['title']}")
            print_meta("Series ID", s["series_id"])
            print_meta("Directory", str(series_dir))
            print_meta("Chapters", str(len(chapters)))
            print_meta("Size", format_bytes(total_size))
            console.print()
            print_dim(f"Dry run: would move '{s['title']}' to trash and forget it.")
        return EXIT_OK

    if as_json:
        # --json disables prompts: a real removal requires -y, mirroring the
        # non-interactive refusal below.
        if not yes:
            print_error("Removing a series requires confirmation.")
            print_dim("Re-run with -y to remove without a prompt.")
            return EXIT_INTERRUPTED
    else:
        console.print()
        print_header(f"Remove series: {s['title']}")
        print_meta("Series ID", s["series_id"])
        print_meta("Directory", str(series_dir))
        print_meta("Chapters", str(len(chapters)))
        print_meta("Size", format_bytes(total_size))
        if not yes:
            try:
                answer = Prompt.ask(
                    "Move this series to trash and remove from library?",
                    choices=["y", "n"],
                    default="n",
                )
            except EOFError:
                console.print()
                return EXIT_INTERRUPTED
            if answer.strip().lower() != "y":
                print_dim("Cancelled.")
                return EXIT_OK

    trash.mkdir(parents=True, exist_ok=True)
    moved = False
    if series_dir.is_dir():
        try:
            shutil.move(str(series_dir), str(dest))
            # mtime otherwise travels with the moved directory (its original
            # creation date), which would let _purge_trash delete a fresh trash
            # entry immediately — or keep a stale one forever. Stamp it now so
            # the TTL counts from removal time.
            with contextlib.suppress(OSError):
                os.utime(dest)
            moved = True
            if not as_json:
                print_success(f"Moved to trash: {dest}")
        except OSError as exc:
            print_error(f"Could not move series directory: {exc}")
            return EXIT_ERROR
    else:
        if not as_json:
            print_dim("Series directory not found on disk; removing library entry only.")

    _write_trash_sidecar(trash, entry_name, s, chapters)

    if library.remove_series(s["series_id"]):
        if not as_json:
            print_success("Removed from library.")
    else:
        print_warning("Could not update the library database.")
    if as_json:
        console.print(json.dumps({
            "schema_version": JSON_SCHEMA_VERSION,
            "series_id": s["series_id"],
            "title": s["title"],
            "directory": str(series_dir),
            "chapter_count": len(chapters),
            "size_bytes": total_size,
            "trashed_to": str(dest),
            "directory_moved": moved,
        }, indent=2), soft_wrap=True)
    elif not as_json:
        print_dim(f"Trash is emptied automatically after {TRASH_TTL_DAYS} days.")
    return EXIT_OK


# ── restore ────────────────────────────────────────────────────

def _cmd_restore(
    library: Library,
    output_dir: Path,
    query: str,
    *,
    dry_run: bool,
    as_json: bool = False,
) -> int:
    if not library.available:
        print_error(f"No library database at {library_path(output_dir)}.")
        print_dim("Download a series first, or point -o at the right output root.")
        return EXIT_ERROR
    entries = _trash_entries(output_dir)
    matches = _match_trash_entries(entries, query)
    if not matches:
        print_error(f"No trashed series matches: {query}")
        print_dim(f"Trash: {_trash_dir(output_dir)}")
        return EXIT_USAGE
    if len(matches) > 1:
        print_error(f"'{query}' matches multiple trashed series:")
        for meta in matches:
            print_dim(
                f"  {meta['series']['series_id']}  {meta['series']['title']}"
            )
        print_dim("Use a series ID to disambiguate.")
        return EXIT_USAGE

    s = matches[0]["series"]
    chapters = matches[0].get("chapters") or []
    series_dir = _resolve_series_dir(output_dir, s)

    output_resolved = output_dir.resolve()
    if not series_dir.is_relative_to(output_resolved):
        print_error("Refusing to restore: series path escapes the output root.")
        return EXIT_ERROR
    if series_dir == output_resolved:
        print_error("Refusing to restore: series path is the output root itself.")
        return EXIT_ERROR
    if library.get_series(s["series_id"]) is not None:
        print_error(f"'{s['title']}' is already in the library.")
        return EXIT_ERROR
    if series_dir.exists():
        print_error(f"Refusing to restore: {series_dir} already exists.")
        return EXIT_ERROR

    console.print()
    if as_json:
        if dry_run:
            console.print(json.dumps({
                "schema_version": JSON_SCHEMA_VERSION,
                "dry_run": True,
                "series_id": s["series_id"],
                "title": s["title"],
                "directory": str(series_dir),
                "chapter_count": len(chapters),
            }, indent=2), soft_wrap=True)
            return EXIT_OK
    else:
        print_header(f"Restore series: {s['title']}")
        print_meta("Series ID", s["series_id"])
        if s.get("source"):
            print_meta("Source", s["source"])
        print_meta("Directory", str(series_dir))
        print_meta("Chapters", str(len(chapters)))
        if dry_run:
            console.print()
            print_dim("Dry run: would restore the directory and library entry.")
            return EXIT_OK

    entry = _trash_dir(output_dir) / matches[0]["entry"]
    restored_dir = False
    if entry.is_dir():
        series_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(entry), str(series_dir))
        except OSError as exc:
            print_error(f"Could not move directory out of trash: {exc}")
            return EXIT_ERROR
        restored_dir = True
        if not as_json:
            print_success(f"Restored directory: {series_dir}")
    else:
        if not as_json:
            print_dim("Series directory not found in trash; restoring library entry only.")

    library.restore_series(s, chapters)
    matches[0]["_sidecar"].unlink(missing_ok=True)
    if as_json:
        console.print(json.dumps({
            "schema_version": JSON_SCHEMA_VERSION,
            "series_id": s["series_id"],
            "title": s["title"],
            "directory": str(series_dir),
            "chapter_count": len(chapters),
            "directory_restored": restored_dir,
        }, indent=2), soft_wrap=True)
    else:
        print_success("Restored to library.")
        print_dim(f"Trash is emptied automatically after {TRASH_TTL_DAYS} days.")
    return EXIT_OK


# ── helpers ────────────────────────────────────────────────────

def _resolve_series(library: Library, query: str) -> dict | None:
    matches = library.find_series(query)
    if not matches:
        print_error(f"Series not found: {query}")
        domain = _detect_source_domain(query)
        if domain is not None:
            print_dim(
                f"'{query}' looks like a source, not a series. Filter the "
                f"library instead: comic-dl list -o <dir> --source {domain}"
            )
        elif query.lstrip().lower().startswith(("http://", "https://")):
            print_dim(
                "No series in this library matches that URL. It may be in a "
                "different -o root, or you may have passed a chapter URL."
            )
        else:
            title = _suggest_series_title(library, query)
            if title is not None:
                print_dim(f"Did you mean: {title}?")
                print_dim(f"Try: comic-dl info -o <dir> '{title}'")
            elif _looks_like_domain(query):
                print_dim(
                    f"'{query}' looks like a web address or source domain, "
                    "not a series title. Use 'list' to see what's in the "
                    "library."
                )
            else:
                print_dim("Use 'list' to see what's in the library.")
        return None
    if len(matches) > 1:
        print_error(f"'{query}' matches multiple series:")
        for m in matches:
            print_dim(f"  {m['series_id']}  {m['title']}")
        print_dim("Use a series ID to disambiguate.")
        return None
    return matches[0]


def _resolve_series_dir(output_dir: Path, s: dict) -> Path:
    rel = (s.get("relative_path") or "").strip()
    if rel:
        return (output_dir / rel).resolve()
    return (output_dir / sanitize_filename(s.get("title") or "")).resolve()


def _trash_dir(output_dir: Path) -> Path:
    return output_dir / ".comic-dl" / "trash"


def _write_trash_sidecar(
    trash: Path,
    entry_name: str,
    series: dict,
    chapters: list[dict],
) -> None:
    """Snapshot a removed series into the trash for later ``restore``.

    The sidecar is a sibling of the moved directory named
    ``<entry>.restore.json``; it records the series row (including
    timestamps) and every chapter row, so a restore can rebuild the library
    entry exactly as it was and is not blocked when the directory itself is
    missing on disk.
    """
    try:
        payload = {
            "entry": entry_name,
            "series": {k: series.get(k) for k in (
                "series_id", "title", "source", "source_site",
                "relative_path", "last_checked", "last_updated", "created_at",
            )},
            "chapters": chapters,
        }
        (trash / f"{entry_name}{RESTORE_SIDECAR_SUFFIX}").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _trash_entries(output_dir: Path) -> list[dict]:
    """Return the sidecar metadata of every removable trash entry."""
    trash = _trash_dir(output_dir)
    if not trash.is_dir():
        return []
    entries: list[dict] = []
    for sidecar in sorted(trash.glob(f"*{RESTORE_SIDECAR_SUFFIX}")):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or "series" not in meta:
            continue
        meta["_sidecar"] = sidecar
        entries.append(meta)
    return entries


def _match_trash_entries(entries: list[dict], query: str) -> list[dict]:
    """Resolve ``query`` against trashed-series sidecars.

    Mirrors :func:`_resolve_series`: exact series_id, exact normalized
    source URL, case-insensitive title exact, then substring. Returns every
    hit so callers can detect ambiguity.
    """
    q = query.strip()
    if not q:
        return []
    exact_url = normalize_url(q) if q.lower().startswith(("http://", "https://")) else ""
    exact_title: list[dict] = []
    substring: list[dict] = []
    for meta in entries:
        s = meta["series"]
        title = s.get("title") or ""
        if s.get("series_id") == q:
            return [meta]
        if exact_url and normalize_url(s.get("source") or "") == exact_url:
            return [meta]
        if title.lower() == q.lower():
            exact_title.append(meta)
        elif q.lower() in title.lower():
            substring.append(meta)
    return exact_title or substring


def _purge_trash(output_dir: Path) -> None:
    """Delete trash entries and sidecars older than ``TRASH_TTL_DAYS``."""
    trash = _trash_dir(output_dir)
    if not trash.is_dir():
        return
    cutoff = datetime.now(UTC).timestamp() - TRASH_TTL_DAYS * 86400
    try:
        for entry in trash.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                elif (
                    entry.is_file()
                    and entry.name.endswith(RESTORE_SIDECAR_SUFFIX)
                    and entry.stat().st_mtime < cutoff
                ):
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        return


def _num_key(value: str):
    try:
        return (0, float(value))
    except (TypeError, ValueError):
        return (1, value)


def _sort_chapters(chapters: list[dict]) -> None:
    chapters.sort(key=lambda c: _num_key(c.get("chapter_no") or ""))

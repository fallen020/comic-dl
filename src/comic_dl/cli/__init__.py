"""Command-line interface: argument parsing, URL routing, and orchestration."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import contextlib
import json
import os
import random
import re
import shutil
import signal

# Launches an external editor (config edit); no shell.
import subprocess  # nosec B404
import sys
import tarfile
import tempfile
import time
import tomllib
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import (
    ConnectionError as CurlConnectionError,
)
from curl_cffi.requests.exceptions import (
    HTTPError as CurlHTTPError,
)
from curl_cffi.requests.exceptions import (
    Timeout as CurlTimeout,
)
from rich.markup import escape as esc
from rich.prompt import Confirm, Prompt
from rich.rule import Rule

from .. import __version__ as _version
from ..archiver import ARCHIVE_PATTERNS, parse_compression
from ..comicinfo import generate_series_comicinfo_xml
from ..config import (
    DEFAULT_CONFIG_TOML,
    config_path,
    configured_output_dir,
    download_setting,
    effective_config,
    generic_enabled,
    http_setting,
    load_config,
    set_config_path,
    set_no_config,
    set_runtime_download,
    set_runtime_http,
)
from ..downloader import (
    PAGE_PARALLEL_CEILING,
    DownloadPipeline,
    active_partial_files,
    close_shared_cover_session,
    download_cover_to,
    probe_download_size,
)
from ..errors import (
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    ComicError,
    ScrapeTimeout,
)
from ..library import Library, library_path, source_id
from ..models import PostMetadata
from ..platform import default_editor as _default_editor
from ..rate import rate_limiting_enabled
from ..scrapers import get_entry, list_sources, load_plugins
from ..scrapers.registry import (
    get_chapter_scraper,
    get_generic_scraper,
    get_series_scraper,
)
from ..scrapers.sites.asurascans import (
    is_chapter_url as is_asurascans_chapter_url,
)
from ..scrapers.sites.asurascans import (
    is_series_url as is_asurascans_series_url,
)
from ..scrapers.sites.flamecomics import (
    is_chapter_url as is_flamecomics_chapter_url,
)
from ..scrapers.sites.flamecomics import (
    is_series_url as is_flamecomics_series_url,
)
from ..scrapers.sites.fsicomics import (
    is_chapter_url as is_fsicomics_chapter_url,
)
from ..scrapers.sites.fsicomics import (
    is_series_url as is_fsicomics_series_url,
)
from ..scrapers.sites.gedecomix import (
    is_chapter_url as is_gedecomix_chapter_url,
)
from ..scrapers.sites.gedecomix import (
    is_series_url as is_gedecomix_series_url,
)
from ..scrapers.sites.kagane import (
    is_chapter_url as is_kagane_chapter_url,
)
from ..scrapers.sites.kagane import (
    is_series_url as is_kagane_series_url,
)
from ..scrapers.sites.webtoon import (
    is_chapter_url as is_webtoon_chapter_url,
)
from ..scrapers.sites.webtoon import (
    is_series_url as is_webtoon_series_url,
)
from ..scrapers.sites.webtoon import (
    normalize_webtoon_url,
)
from ..ui import (
    DIAGNOSTIC,
    ERROR,
    JSON_SCHEMA_VERSION,
    MUTED,
    NORMAL,
    TAG_DOWNLOAD,
    TAG_SCRAPE,
    TAG_TIMING,
    TRACE,
    VERBOSE,
    VERBOSITY,
    Activity,
    ComicArgumentParser,
    SourceRow,
    _active_console,
    _classify,
    _source_row_text,
    active_snapshot,
    apply_color_mode,
    checkbox_prompt,
    console,
    err_console,
    flush_debug_file,
    format_bytes,
    get_ui_gate,
    glyphs,
    is_interactive,
    print_banner,
    print_batch_summary,
    print_chapter_preview,
    print_dim,
    print_error,
    print_error_detail,
    print_failure_recap,
    print_header,
    print_help,
    print_help_summary,
    print_interrupt,
    print_meta,
    print_partial_block,
    print_partial_recap,
    print_retry,
    print_skipped,
    print_success,
    print_summary,
    print_url,
    print_warning,
    render_sources_table,
    report_error,
    run_with_status,
    set_debug_file,
    set_json_mode,
    set_verbosity,
    source_search,
    suggest,
    teardown_active,
    trace,
    verbosity,
    vlog,
)
from ..utils import (
    RequestBlockedError,
    _with_referer,
    ensure_unique_dir,
    impersonate_is_deprecated,
    normalize_url,
    normalize_url_key,
    sanitize_filename,
    validate_impersonate,
)
from ..utils import (
    cbz_source_url as _cbz_source_url,
)
from .library import COMMANDS as _LIBRARY_COMMANDS
from .library import _resolve_series, run_library_command
from .selection import (
    ChapterSelection,
    ChapterSelectionQuit,
    parse_chapter_selection,
    validate_chapter_flag,
)
from .sizing import (
    UNKNOWN_PAGE_BYTES_GUESS,
    _check_disk_space,
    _estimate_download_bytes,
    _parse_size,
    format_option_size,
)

_TMP_ROOT: Path | None = None

MAX_CONCURRENCY = 32
MAX_PARALLEL = 16
MAX_CHAPTER_PARALLEL = 8
MAX_CLIENTS = 512
MAX_URL_LENGTH = 2048
MAX_URLS_PER_RUN = 2000


def _tmp_root() -> Path:
    """Per-run scratch root for chapter staging, allocated with mkdtemp.

    A predictable shared path (``/tmp/comic-dl``) would let any local user
    pre-create or symlink chapter work dirs and files under it; ``mkdtemp``
    gives this process a 0700 directory with an unpredictable name instead.
    ``[download] tmp-dir`` overrides the parent directory (default: system
    temp) for runs whose system temp is small or RAM-backed.
    Allocated lazily once per run and removed by :func:`_cleanup_temp_dir`.
    """
    global _TMP_ROOT
    if _TMP_ROOT is None:
        scratch = download_setting("tmp-dir", None)
        parent: Path | None = None
        if isinstance(scratch, str) and scratch.strip():
            parent = Path(scratch.strip()).expanduser()
            parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {}
        if parent is not None:
            kwargs["dir"] = str(parent)
        _TMP_ROOT = Path(tempfile.mkdtemp(prefix="comic-dl-", **kwargs))
    return _TMP_ROOT


def _prompt_chapter_selection(
    chapters: list[dict],
    series_title: str,
    have_urls: set[str],
) -> set[int] | None:
    """Run the interactive checkbox selector for a series.

    Returns the chosen 1-based chapter numbers, or ``None`` when the user
    cancels (q / Esc). The caller owns the :class:`Activity` pause/resume.
    """
    options: list[tuple[int, str]] = []
    for idx, ch in enumerate(chapters, start=1):
        label = ch.get("title") or ch.get("episode_no") or f"Chapter {idx}"
        options.append((idx, label))
    new_count = len(chapters) - sum(
        1 for c in chapters if normalize_url_key(c.get("url") or "") in have_urls
    )
    total_word = "chapter" if len(chapters) == 1 else "chapters"
    title = (
        f"Select chapters — {series_title} "
        f"({len(chapters)} {total_word}, {new_count} new)"
    )
    return checkbox_prompt(title, options)


_HTTP_ERROR_REASONS: dict[int, str] = {
    403: "Access blocked (403) — the site may be blocking automated requests",
    404: "Not found (404) — the URL may be dead or mistyped",
    429: "Rate limited (429) after retries — try again later or lower --concurrency",
    451: "Unavailable for legal reasons (451)",
}


def _scrape_retry_sleep(exc: Exception, attempt: int) -> float:
    """Delay before a metadata retry after an HTTP error.

    Honours the RFC 7231 ``Retry-After`` header on the failed response
    (capped exactly like the image engine's), so a rate-limited scrape backs
    off for as long as the server asked instead of burning a fixed ramp.
    Falls back to the ``(1 + attempt) * 0.5`` schedule when no header is
    present; never removes politeness that the plain ramp already provided.
    """
    from ..downloader import _retry_after_wait_seconds

    retry_after = _retry_after_wait_seconds(
        getattr(getattr(exc, "response", None), "headers", None)
    )
    if retry_after is not None:
        return retry_after
    return (1.0 + attempt) * 0.5


class _HelpAction(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        # -h / -? → compact summary; --help → full page
        if option_string in ("-h", "-?"):
            print_help_summary()
        else:
            print_help()
        sys.exit(EXIT_OK)


class _VersionAction(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        console.print(f"comic-dl {_version}")
        sys.exit(EXIT_OK)


def _strip_inline_comment(line: str) -> str:
    """Remove a trailing ``# comment`` from a URL-list line.

    Only a ``#`` preceded by whitespace starts a comment, so a URL fragment
    (``https://a.com/#page2``) is never truncated. Full-line comments are
    handled by the caller before this runs.
    """
    if "#" not in line:
        return line
    for i, char in enumerate(line):
        if char == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line


def _validate_list_url(raw: str) -> bool:
    """Whether ``raw`` is an acceptable URL for a ``-f`` list.

    Mirrors the ``-u`` gate: the line must be an ``http://`` or ``https://``
    URL with a host. ``normalize_url`` prefixes a scheme when one is missing,
    so the check runs on the raw line before normalization. URLs longer than
    :data:`MAX_URL_LENGTH` are rejected so an oversized line can't blow past
    downstream buffers.
    """
    stripped = raw.strip()
    if len(stripped) > MAX_URL_LENGTH:
        return False
    parsed = urlparse(stripped)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _read_urls_from_file_indexed(path: Path) -> list[tuple[str, int]] | None:
    """Read a URL-list file, skipping blanks and ``#`` comments.

    Inline ``# comment`` suffixes are stripped (see :func:`_strip_inline_comment`).
    Lines that do not parse as ``http(s)`` URLs are skipped with a warning
    naming the file and line, rather than failing downstream as a download
    task. Returns ``(url, line_number)`` pairs with 1-based line numbers;
    duplicate URLs (compared by :func:`normalize_url` identity) keep the first
    occurrence's spelling, order, and line number. ``None`` if the file
    cannot be read or decoded.

    The file is streamed line by line (never slurped whole) and capped at
    :data:`MAX_URLS_PER_RUN` URLs, so a runaway list can't exhaust memory or
    spawn an unbounded batch.
    """
    urls: list[tuple[str, int]] = []
    seen: set[str] = set()
    try:
        with path.open(encoding="utf-8") as fh:
            for idx, line in enumerate(fh, start=1):
                raw = _strip_inline_comment(line).strip()
                if not raw or raw.startswith("#"):
                    continue
                if not _validate_list_url(raw):
                    if len(raw) > MAX_URL_LENGTH:
                        print_warning(
                            f"{path}:{idx}: skipping URL longer than "
                            f"{MAX_URL_LENGTH} characters"
                        )
                    else:
                        print_warning(
                            f"{path}:{idx}: skipping invalid URL {raw!r} "
                            "(must start with http:// or https://)"
                        )
                    continue
                key = normalize_url_key(raw)
                if key in seen:
                    continue
                seen.add(key)
                urls.append((raw, idx))
                if len(urls) >= MAX_URLS_PER_RUN:
                    print_warning(
                        f"{path}: stopping at {MAX_URLS_PER_RUN} URLs "
                        f"(--file limit)."
                    )
                    break
    except (OSError, UnicodeDecodeError):
        return None
    return urls


def _read_urls_from_file(path: Path) -> list[str] | None:
    """Read a URL-list file, skipping blanks and ``#`` comments.

    Duplicate URLs (compared by :func:`normalize_url` identity) are dropped,
    keeping the first occurrence's spelling and the file's order.
    """
    indexed = _read_urls_from_file_indexed(path)
    if indexed is None:
        return None
    return [url for url, _ in indexed]


def _existing_archives(series_dir: Path) -> dict[str, Path]:
    """Map ``stem -> archive path`` for every comic archive in ``series_dir``.

    Covers all supported formats (``.cbz``/``.zip``/``.cbt``) so resolution
    treats any existing archive as "already downloaded": switching formats
    must never produce a second copy. When multiple archives share a stem the
    first match wins (deterministic; ``.cbz`` preferred over ``.zip``/``.cbt``).
    """
    found: dict[str, Path] = {}
    for pattern in ARCHIVE_PATTERNS:
        for p in series_dir.glob(pattern):
            found.setdefault(p.stem, p)
    return found


def _resolve_archive_path(
    series_dir: Path,
    chapter_title: str,
    url: str,
    post_id: str,
    force: bool,
    quiet: bool,
    fmt: str,
) -> Path | None:
    """Pick the file to download into, or None if it already exists.

    A filename is not final until the post is identified: an existing archive
    matching this URL means the same post is already downloaded (skip), while
    a different post with the same title is written to a disambiguated
    `Title (post_id).<ext>`. Any existing archive format counts as "already
    downloaded", so a format switch does not download a duplicate.
    """
    ext = f".{fmt}"
    base_stem = sanitize_filename(chapter_title)
    base = series_dir / f"{base_stem}{ext}"
    existing = _existing_archives(series_dir)
    if force or base_stem not in existing:
        # A disambiguated copy of this post may exist even when the plain
        # name does not (e.g. the plain file was removed) — never re-download.
        if not force and post_id and f"{base_stem} ({post_id})" in existing:
            if not quiet:
                print_skipped(
                    f"{existing[f'{base_stem} ({post_id})'].name} already exists. Skipping."
                )
            return None
        return base

    picked = existing[base_stem]
    if _is_partial(picked):
        if not quiet:
            print_skipped(
                f"{picked.name} was incomplete. Re-downloading missing pages."
            )
        return picked

    if _cbz_source_url(picked).rstrip("/") == url.rstrip("/"):
        if not quiet:
            print_skipped(f"{picked.name} already exists. Skipping.")
        return None

    if post_id:
        disambig_stem = f"{base_stem} ({post_id})"
        if disambig_stem in existing:
            if not quiet:
                print_skipped(
                    f"{existing[disambig_stem].name} already exists. Skipping."
                )
            return None
        return series_dir / f"{disambig_stem}{ext}"

    if not quiet:
        print_skipped(f"{picked.name} already exists. Skipping.")
    return None


def _partial_marker(cbz_path: Path) -> Path:
    """Path of the marker file marking a partially-downloaded chapter.

    A CBZ with a ``.cbz.partial`` marker next to it is treated as *not*
    downloaded: a rerun retries the missing pages into the same file. The
    marker is removed once the chapter finishes completely.
    """
    return cbz_path.with_name(f"{cbz_path.name}.partial")


def _is_partial(cbz_path: Path) -> bool:
    return _partial_marker(cbz_path).is_file()


_PAGE_NAME_RE = re.compile(r"^Page_\d+\.[A-Za-z0-9]{1,5}$")

#: Hard cap on any single page restored from an old archive (zip-bomb guard;
#: pages larger than this were never accepted by the downloader either).
_RESTORE_PAGE_CAP = 512 * 1024 * 1024


def _restore_pages_from_archive(archive_path: Path, dest_dir: Path) -> int:
    """Copy the intact pages of an incomplete archive back into ``dest_dir``.

    A rerun on a ``.partial`` chapter used to start from an empty temp dir,
    so every page looked missing and the whole gallery re-downloaded.
    Restoring the previous run's good pages first lets the normal
    skip-if-present download check stand in for them — only genuinely
    missing pages hit the network.

    Only flat ``Page_NNNN.ext`` members are extracted (never ComicInfo.xml
    or nested paths). Returns the number of pages restored; a corrupt or
    unreadable archive restores nothing and the rerun falls back to a full
    download.
    """
    count = 0

    def _acceptable(info_name: str, size: int | None) -> bool:
        if not _PAGE_NAME_RE.match(info_name):
            return False
        if size is not None and size > _RESTORE_PAGE_CAP:
            trace(f"resume: {info_name} exceeds restore cap, skipping")
            return False
        return True

    try:
        if archive_path.suffix.lower() == ".cbt":
            with tarfile.open(archive_path) as tf:
                for info in tf.getmembers():
                    if not info.isfile() or not _acceptable(
                        info.name, info.size
                    ):
                        continue
                    src = tf.extractfile(info)
                    if src is None:
                        continue
                    (dest_dir / info.name).write_bytes(src.read())
                    count += 1
        else:
            with ZipFile(archive_path) as zf:
                for zinfo in zf.infolist():
                    if not _acceptable(zinfo.filename, zinfo.file_size):
                        continue
                    (dest_dir / zinfo.filename).write_bytes(zf.read(zinfo))
                    count += 1
    except (BadZipFile, tarfile.TarError, OSError) as exc:
        trace(f"resume: could not restore from {archive_path.name}: {exc}")
        return 0
    vlog(
        DIAGNOSTIC,
        f"resume: restored {count} page(s) from {archive_path.name}",
        tag=TAG_DOWNLOAD,
    )
    return count


_TEXT_SOURCE_PREFIX = "<!-- source: "
_TEXT_SOURCE_SUFFIX = " -->"


def _write_series_comicinfo(
    series_dir: Path,
    *,
    series_title: str,
    source_url: str,
    description: str,
    meta: object | None,
) -> None:
    """Write the series-level ComicInfo.xml beside the chapter archives.

    ``meta`` is a scraped chapter (whose authors/genres/publisher/etc. are
    really series-level facts) when one is available; otherwise the file is
    written from the series listing alone.
    """
    series_dir.joinpath("ComicInfo.xml").write_text(
        generate_series_comicinfo_xml(
            series_title=series_title,
            source_url=source_url,
            description=description,
            authors=list(getattr(meta, "authors", None) or []),
            artists=list(getattr(meta, "artists", None) or []),
            colorists=list(getattr(meta, "colorists", None) or []),
            genres=list(getattr(meta, "genres", None) or []),
            publisher=getattr(meta, "publisher", None),
            status=getattr(meta, "status", None),
            language=getattr(meta, "language", None),
            reading_direction=getattr(meta, "reading_direction", None),
            community_rating=getattr(meta, "community_rating", None),
            year=getattr(meta, "year", None),
        ),
        encoding="utf-8",
    )


def _iter_library_files(output_dir: Path, pattern: str):
    """Yield ``pattern`` matches under ``output_dir``, skipping hidden dirs.

    ``.comic-dl/`` (the internal DB + trash) and any other dot-directory are
    never treated as downloadable content.
    """
    for path in output_dir.rglob(pattern):
        rel = path.relative_to(output_dir).parts
        if rel and rel[0].startswith("."):
            continue
        yield path


def _build_downloaded_index(output_dir: Path) -> dict[str, Path]:
    """Map normalized source URL -> downloaded file under ``output_dir``.

    The Library DB is the primary source of truth (no zip/scan I/O for
    recorded downloads). Any ``*.cbz`` / ``*.md`` on disk *not* already
    covered by the DB is scanned as a fallback, so files from before the DB
    existed are still found. ``.comic-dl/`` is always skipped. Lets
    :func:`_run_urls` skip already-downloaded URLs before any network I/O.
    Returns ``{}`` when ``output_dir`` is missing.
    """
    index: dict[str, Path] = {}
    if not output_dir.is_dir():
        return index

    library = Library(library_path(output_dir))
    library.open()
    try:
        if library.available:
            index = library.downloaded_index(output_dir)
    finally:
        library.close()

    known_paths = set(index.values())
    for pattern in ARCHIVE_PATTERNS:
        for path in _iter_library_files(output_dir, pattern):
            if path in known_paths or _is_partial(path):
                continue
            url = _cbz_source_url(path)
            if not url.startswith(("http://", "https://")):
                continue
            key = normalize_url_key(url)
            if key:
                index[key] = path

    # Any partially-downloaded CBZ (regardless of how it was indexed) must not
    # pre-skip a rerun: the missing pages still need to be fetched.
    for key, path in list(index.items()):
        if _is_partial(path):
            del index[key]

    for path in _iter_library_files(output_dir, "*.md"):
        try:
            with path.open("r", encoding="utf-8") as fh:
                first = fh.readline()
        except OSError:
            continue
        stripped = first.strip()
        if not (
            stripped.startswith(_TEXT_SOURCE_PREFIX)
            and stripped.endswith(_TEXT_SOURCE_SUFFIX)
        ):
            continue
        url = stripped[len(_TEXT_SOURCE_PREFIX) : -len(_TEXT_SOURCE_SUFFIX)].strip()
        if not url.startswith(("http://", "https://")):
            continue
        key = normalize_url_key(url)
        if key:
            index[key] = path
    return index


def _ensure_nomedia(output_dir: Path) -> None:
    """Create an Android gallery ``.nomedia`` marker (idempotent, never clobber)."""
    marker = output_dir / ".nomedia"
    if not marker.exists():
        marker.touch()


def _conf_int(value: object, fallback: int, key: str = "concurrency") -> int:
    if isinstance(value, bool):
        if value:
            _warn_config(key, value)
        return fallback
    if isinstance(value, int):
        return value if value >= 1 else fallback
    if isinstance(value, str):
        try:
            n = int(value)
        except ValueError:
            _warn_config(key, value)
            return fallback
        return n if n >= 1 else fallback
    if value is not None:
        _warn_config(key, value)
    return fallback


def _conf_size(value: object, fallback: int, key: str = "max-size") -> int:
    if isinstance(value, bool):
        if value:
            _warn_config(key, value)
        return fallback
    if isinstance(value, int):
        return value if value > 0 else fallback
    if isinstance(value, str):
        try:
            return _parse_size(value)
        except (ValueError, argparse.ArgumentTypeError):
            _warn_config(key, value)
            return fallback
    if value is not None:
        _warn_config(key, value)
    return fallback


def _warn_config(key: str, value: object) -> None:
    print_warning(f"Invalid {key} in config file ({value!r}); using default.")


def _normalize_format(value: str) -> str:
    """Canonicalize a ``--format`` / ``[archive] format`` value.

    Accepts ``cbz`` (default), ``zip``, and ``cbt``, case-insensitively.
    Anything else raises ``ValueError`` so a typo fails loudly at startup
    instead of silently producing a different archive.
    """
    v = (value or "").strip().lower()
    if v not in {"cbz", "zip", "cbt"}:
        raise ValueError(f"Invalid format {value!r}: expected cbz, zip, or cbt")
    return v


def _apply_config(args: argparse.Namespace) -> None:
    """Resolve CLI defaults against the config file.

    Precedence: CLI flag > config.toml > built-in default.
    """
    conf = load_config()
    if args.output is None:
        args.output = configured_output_dir()
    if args.concurrency is None:
        args.concurrency = _conf_int(conf.get("concurrency"), 5)
    if args.parallel is None:
        args.parallel = _conf_int(conf.get("parallel"), 5, key="parallel")
    if args.chapter_parallel is None:
        args.chapter_parallel = _conf_int(
            conf.get("chapter_parallel"), 1, key="chapter_parallel"
        )
    if args.max_image_size is None:
        args.max_image_size = _conf_size(
            conf.get("max_image_size"), 100 * 1024 * 1024, key="max-image-size",
        )
    if args.max_size is None:
        args.max_size = _conf_size(conf.get("max_size"), 0)
    if getattr(args, "compress", None) is None:
        archive_cfg = conf.get("archive")
        cfg_compression = (
            archive_cfg.get("compression")
            if isinstance(archive_cfg, dict)
            else None
        )
        args.compress = cfg_compression if isinstance(cfg_compression, str) else "stored"
    if getattr(args, "format", None) is None:
        archive_cfg = conf.get("archive")
        cfg_format = (
            archive_cfg.get("format") if isinstance(archive_cfg, dict) else None
        )
        args.format = cfg_format if isinstance(cfg_format, str) else "cbz"

    # Runtime HTTP overrides from CLI flags: apply whatever the user explicitly
    # passed so config helpers ([http] keys) honour flag > config precedence.
    runtime_http: dict[str, Any] = {}
    if args.impersonate is not None:
        runtime_http["impersonate"] = args.impersonate
    if args.solver is not None:
        runtime_http["solver"] = args.solver
    if args.no_cookie:
        runtime_http["cookie-jar"] = False
    if args.no_cache:
        runtime_http["cache"] = False
    if args.no_rate:
        runtime_http["rate-enabled"] = False
    if runtime_http:
        set_runtime_http(**runtime_http)
    if args.no_generic:
        set_runtime_download(generic=False)

    # Make rate-limiting state explicit: a run with throttling disabled can
    # hammer a site, so surface it as a warning rather than silently.
    if not rate_limiting_enabled():
        print_warning("Per-site rate limiting is disabled for this run.")
        if args.concurrency > PAGE_PARALLEL_CEILING:
            print_warning(
                f"Page concurrency clamped to {PAGE_PARALLEL_CEILING} while "
                "rate limiting is off (recommended ceiling)."
            )

    # Validate the effective impersonation profile up front (flag > config >
    # built-in) so a typo is caught before the first request, and warn when a
    # valid-but-very-old profile is used.
    effective_impersonate = args.impersonate or http_setting("impersonate", "chrome146")
    if isinstance(effective_impersonate, str) and effective_impersonate.strip():
        problem = validate_impersonate(effective_impersonate.strip())
        if problem:
            print_warning(f"{problem}; using chrome146 instead.")
            # Reset both the CLI arg and the runtime override so the config's
            # bad value can't resurface mid-run via http_setting().
            args.impersonate = "chrome146"
            set_runtime_http(impersonate="chrome146")
        elif impersonate_is_deprecated(effective_impersonate.strip()):
            print_warning(
                f"Impersonation profile {effective_impersonate.strip()!r} is "
                "outdated — sites may reject it. Consider chrome146 or newer."
            )

    # Validate the effective CBZ compression up front so a typo in
    # --compress/[archive] compression fails before any downloading.
    try:
        parse_compression(args.compress)
    except ValueError as exc:
        print_error(f"{exc}")
        sys.exit(EXIT_USAGE)

    # Validate the effective archive format up front too.
    try:
        args.format = _normalize_format(args.format)
    except ValueError as exc:
        print_error(f"{exc}")
        sys.exit(EXIT_USAGE)


def _build_first_stage_parser() -> ComicArgumentParser:
    """Construct the first-stage argument parser.

    Extracted from :func:`parse_urls` so the shell-completion generator can
    derive the global flag list straight from argparse.
    """
    parser = ComicArgumentParser(
        prog="comic-dl",
        description="",
        add_help=False,
    )

    parser.add_argument("-h", "--help", action=_HelpAction)
    parser.add_argument("-?", action=_HelpAction)
    parser.add_argument("--version", action=_VersionAction)

    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List registered sources (built-in and plugins) and exit",
    )

    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--url", "-u",
        metavar="URL",
        help="Download a single gallery URL",
    )
    source.add_argument(
        "--file", "-f",
        type=Path,
        metavar="FILE",
        help="Download URLs from a text file (errors cite file:line)",
    )

    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        metavar="DIR",
        help="Output directory (default: ~/Downloads/comic-dl)",
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=None,
        metavar="N",
        help="Max parallel chapter downloads (1-32; default 5)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=None,
        help="Max URLs in flight across a batch (1-16; default 5)",
    )
    parser.add_argument(
        "--chapter-parallel",
        type=int,
        default=None,
        help="Max chapters of a series downloading at once (1-8; default 1)",
    )
    parser.add_argument(
        "--impersonate",
        default=None,
        help=(
            "TLS/HTTP impersonation profile (e.g. chrome131, chrome146); "
            "overrides [http] impersonate"
        ),
    )
    parser.add_argument(
        "--solver",
        choices=["auto", "impersonation", "webview", "off"],
        default=None,
        help=(
            "Cloudflare challenge solver (auto/impersonation/webview/off); "
            "overrides [http] solver"
        ),
    )
    parser.add_argument(
        "--no-cookie",
        action="store_true",
        help="Disable the persistent cookie jar for this run",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the on-disk scrape response cache for this run",
    )
    parser.add_argument(
        "--no-rate",
        action="store_true",
        help="Disable per-site rate limiting for this run",
    )
    parser.add_argument(
        "--no-generic",
        action="store_true",
        help=(
            "Disable the generic fallback scraper for this run (unknown hosts "
            "report Unsupported URL instead of static extraction)"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing CBZ files (conflicts with --no-clobber)",
    )
    parser.add_argument(
        "--no-clobber",
        action="store_true",
        help="Never overwrite existing CBZ files (default; conflicts with --force)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Preview: resolve metadata for each URL (chapter vs series) and "
            "list what would download/skip/redownload, without writing"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON on stdout; disables interactive prompts",
    )
    parser.add_argument(
        "--max-image-size",
        type=_parse_size,
        default=None,
        metavar="SIZE",
        help="Maximum bytes per image (e.g. 100MB; default 100MB)",
    )
    parser.add_argument(
        "--max-size",
        type=_parse_size,
        default=None,
        metavar="SIZE",
        help="Maximum total download size per batch (e.g. 2GB; default unlimited)",
    )
    parser.add_argument(
        "--compress",
        nargs="?",
        const="deflate",
        default=None,
        metavar="MODE",
        help=(
            "CBZ compression: stored (default) | deflate | deflate:0-9 "
            "(e.g. --compress deflate:9). Overrides [archive] compression"
        ),
    )
    parser.add_argument(
        "--format",
        choices=("cbz", "zip", "cbt"),
        default=None,
        metavar="FORMAT",
        help=(
            "Archive format: cbz (default) | zip | cbt. cbz is the most "
            "widely supported; zip and cbt are plain zip/tar containers. Overrides "
            "[archive] format"
        ),
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress and status output",
    )
    verbosity.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase diagnostic verbosity (-v, -vv, -vvv)",
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="Suppress the ASCII brand banner (default: shown only on an interactive terminal)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in all output, including progress",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default=None,
        metavar="MODE",
        help=(
            "When to color output: auto (default; honors NO_COLOR, "
            "CLICOLOR_FORCE, CLICOLOR, FORCE_COLOR), always (for | less -R), "
            "or never"
        ),
    )
    config_src = parser.add_mutually_exclusive_group()
    config_src.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to a custom config.toml (overrides $COMIC_DL_CONFIG and "
            "the default location)"
        ),
    )
    config_src.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore config.toml for this run (built-in defaults only)",
    )
    parser.add_argument(
        "--debug-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Divert -vvv trace diagnostics to PATH instead of the screen",
    )
    parser.add_argument(
        "--chapters",
        default=None,
        help="Chapter selection, e.g. '1-3,7' or 'all' (interactive picker if omitted)",
    )

    return parser


def parse_urls() -> tuple[list[str], argparse.Namespace]:
    """Build the first-stage parser and return ``(urls, args)``.

    The first-stage parser owns the URL sourcing flags (``--url``/``--file``/
    ``--input``) and the global flags the other stages need. ``urls`` is the
    resolved list of URLs to process.
    """
    parser = _build_first_stage_parser()

    args = parser.parse_args(sys.argv[1:])
    if getattr(args, "config", None) is not None:
        set_config_path(args.config)
    if getattr(args, "no_config", False):
        set_no_config()
    if getattr(args, "no_color", False):
        apply_color_mode("never")
    elif args.color is not None:
        apply_color_mode(args.color)
    _apply_config(args)
    args.urls_from_file = bool(args.file)

    if verbosity() > NORMAL:
        print_dim(f"Config file: {config_path()}")

    if (
        not args.quiet
        and not args.no_banner
        and not args.urls_from_file
        and not args.verbose
        and is_interactive()
    ):
        print_banner()

    if not args.quiet and not args.json:
        print_dim(
            f"Output directory: {Path(args.output).expanduser().resolve()}",
        )

    if args.chapters is not None:
        try:
            validate_chapter_flag(args.chapters)
        except ValueError as exc:
            print_error_detail("Invalid --chapters", str(exc))
            sys.exit(EXIT_USAGE)

    if args.no_clobber and args.force:
        print_error("--no-clobber conflicts with --force.")
        sys.exit(EXIT_USAGE)

    if args.concurrency < 1:
        print_error("--concurrency must be at least 1.")
        sys.exit(EXIT_USAGE)
    if args.concurrency > MAX_CONCURRENCY:
        print_warning(f"--concurrency capped to {MAX_CONCURRENCY}.")
        args.concurrency = MAX_CONCURRENCY

    if args.parallel < 1:
        print_error("--parallel must be at least 1.")
        sys.exit(EXIT_USAGE)
    if args.parallel > MAX_PARALLEL:
        print_warning(f"--parallel capped to {MAX_PARALLEL}.")
        args.parallel = MAX_PARALLEL

    if args.chapter_parallel < 1:
        print_error("--chapter-parallel must be at least 1.")
        sys.exit(EXIT_USAGE)
    if args.chapter_parallel > MAX_CHAPTER_PARALLEL:
        print_warning(f"--chapter-parallel capped to {MAX_CHAPTER_PARALLEL}.")
        args.chapter_parallel = MAX_CHAPTER_PARALLEL

    urls: list[str] | None = []
    url_origins: dict[str, str] | None = None

    if args.url:
        raw = args.url.strip()
        if urlparse(raw).scheme not in {"http", "https"}:
            print_error(
                f"Unsupported URL scheme: '{raw}'. Must start with http:// or "
                "https://. To load URLs from a file, use: -f/--file <PATH>"
            )
            sys.exit(EXIT_USAGE)
        if len(raw) > MAX_URL_LENGTH:
            print_error(
                f"URL exceeds the {MAX_URL_LENGTH}-character maximum "
                f"({len(raw)} chars)."
            )
            sys.exit(EXIT_USAGE)
        urls = [raw]
    elif args.file:
        if not args.file.exists():
            print_error(f"File not found: {args.file}")
            sys.exit(EXIT_USAGE)
        indexed = _read_urls_from_file_indexed(args.file)
        if indexed is None:
            print_error(f"Cannot read file: {args.file}")
            sys.exit(EXIT_USAGE)
        urls = [url for url, _ in indexed]
        if not urls:
            print_error("File is empty or has no valid URLs.")
            sys.exit(EXIT_USAGE)
        url_origins = {url: f"{args.file}:{n}" for url, n in indexed}
        if not args.quiet:
            print_dim(
                f"Loaded {len(urls)} URL"
                f"{'s' if len(urls) != 1 else ''} from {args.file}"
            )
    else:
        if not _is_interactive_output():
            print_error("No URL or URL list file provided.")
            print_dim(
                "Give a URL with -u/--url, or a URL list file with -f/--file."
            )
            sys.exit(EXIT_USAGE)
        try:
            raw = Prompt.ask("[bold]Enter a gallery URL or URL list file[/]")
        except EOFError:
            err_console.print()
            sys.exit(EXIT_INTERRUPTED)
        raw = raw.strip()
        if not raw:
            print_error("No input provided.")
            sys.exit(EXIT_USAGE)
        path = Path(raw)
        if path.exists():
            indexed = _read_urls_from_file_indexed(path)
            if indexed is None:
                print_error(f"Cannot read file: {path}")
                sys.exit(EXIT_USAGE)
            urls = [url for url, _ in indexed]
            if not urls:
                print_error("File is empty or has no valid URLs.")
                sys.exit(EXIT_USAGE)
            url_origins = {url: f"{path}:{n}" for url, n in indexed}
        elif raw.startswith(("http://", "https://")):
            if len(raw) > MAX_URL_LENGTH:
                print_error(
                    f"URL exceeds the {MAX_URL_LENGTH}-character maximum "
                    f"({len(raw)} chars)."
                )
                sys.exit(EXIT_USAGE)
            urls = [raw]
        else:
            guessed = normalize_url(raw)
            if guessed.startswith(("http://", "https://")):
                if len(guessed) > MAX_URL_LENGTH:
                    print_error(
                        f"URL exceeds the {MAX_URL_LENGTH}-character maximum "
                        f"({len(guessed)} chars)."
                    )
                    sys.exit(EXIT_USAGE)
                urls = [guessed]
            else:
                print_error(
                    f"'{raw}' is neither a readable URL list file "
                    "nor an http(s) URL."
                )
                sys.exit(EXIT_USAGE)

    if len(urls) > MAX_URLS_PER_RUN:
        print_error(
            f"Too many URLs for one run: {len(urls)} exceeds the "
            f"{MAX_URLS_PER_RUN}-URL maximum."
        )
        sys.exit(EXIT_USAGE)

    args.url_origins = url_origins
    # A URL list file (whether via -f/--file or an interactively-given path)
    # makes the whole run a non-interactive batch: no banner, no picker prompts.
    args.urls_from_file = url_origins is not None or args.file is not None
    return urls, args


_T = TypeVar("_T")

async def _with_spinner(desc: str, quiet: bool, coro: Coroutine[None, None, _T]) -> _T:
    if quiet:
        return await coro
    # Render at most one animated spinner/status line to the console at a time
    # even when the series flow scrapes several chapters concurrently.
    await get_ui_gate().acquire()
    try:
        return await run_with_status(desc, coro)
    finally:
        get_ui_gate().release()


@contextlib.asynccontextmanager
async def _chapter_activity(
    activity: Activity | None,
    row_key: str,
    *,
    quiet: bool,
    label: str,
    url: str,
):
    """Provide the main status row for one chapter URL.

    With a shared batch ``activity``, reuse one of its rows (labeled with the
    URL) instead of opening a new ``Activity`` — the batch already owns the
    single Live, so parallelism is not serialized by the UI gate. Otherwise
    open a dedicated ``Activity`` and print the per-URL header block, exactly
    as before.
    """
    if activity is not None:
        sink = activity.row(row_key)
        sink.set_label(label or row_key)
        yield sink
        return
    if not quiet:
        err_console.print()
        err_console.print(Rule(style="dim"))
        if label:
            print_header(f"[{label}] Processing")
        else:
            print_header("Processing")
        print_url(url)
    act = Activity(quiet=quiet)
    async with act:
        yield act.row("main")


def _extract_domain(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _source_id_for(domain: str) -> str:
    """Stable source identifier for ``domain`` from the sources registry.

    Falls back to hashing the bare domain (no name/version) so the column is
    never empty, even for a scraper that has not registered a source entry.
    """
    entry = get_entry(domain)
    if entry is not None:
        return entry.source_id
    return source_id("", domain, "")


def _chapter_order_key(ch: dict) -> tuple[bool, int]:
    """Chronological sort key for a scraped chapter dict.

    Numeric episode numbers sort ascending first (Prologue/0, Ep.1, Ep.2 ...);
    chapters with no parseable number sort after all numbered ones.
    """
    raw = str(ch.get("episode_no") or "")
    m = re.search(r"\d+", raw)
    if m:
        return (False, int(m.group(0)))
    return (True, 0)


def _source_row(entry) -> SourceRow:
    """Map a registry entry to a display-ready row (series before chapter)."""
    caps = []
    if entry.has_series:
        caps.append("series")
    if entry.has_chapter:
        caps.append("chapter")
    return SourceRow(
        domain=entry.domain,
        name=entry.name,
        capabilities=tuple(caps),
        origin="built-in" if entry.builtin else "plugin",
        version=entry.version,
    )


def _print_supported_sources() -> None:
    """Print supported sources in the unsupported-URL error path."""
    render_sources_table(
        [_source_row(e) for e in list_sources()],
        note=False,
        console_obj=err_console,
    )


def _is_interactive_output() -> bool:
    """True when both stdout and stdin are real TTYs (interactive search)."""
    return console.is_terminal and sys.stdin.isatty()


async def _run_list_sources(argv: list[str] | None = None) -> int:
    """List supported sites — interactive search, plain table, or JSON.

    Routing (matches ``git diff``/``ls --color`` conventions):
    - ``--json`` → structured JSON for scripting, regardless of TTY;
    - stdout and stdin both a TTY → interactive live-search view;
    - otherwise → the plain table (pipes, CI, no-pty SSH).
    ``--plugin`` narrows to third-party sources and a positional ``query``
    filters every mode by substring on domain/name/capabilities/origin.
    """
    parser = ComicArgumentParser(
        prog="comic-dl --list-sources",
        description="List supported sites and exit.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON for scripting")
    parser.add_argument(
        "--plugin", action="store_true", help="only third-party (plugin) sources",
    )
    parser.add_argument(
        "query", nargs="?", default=None,
        help="filter by substring match on domain or name",
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    entries = list_sources()
    if args.plugin:
        entries = [e for e in entries if not e.builtin]
    rows = [_source_row(e) for e in entries]
    if args.query:
        q = args.query.lower()
        rows = [r for r in rows if q in _source_row_text(r)]

    if args.json:
        payload = [{"domain": r.domain, "origin": r.origin} for r in rows]
        console.print(json.dumps(
            {"schema_version": JSON_SCHEMA_VERSION, "sources": payload}, indent=2,
        ), soft_wrap=True)
        return EXIT_OK

    if _is_interactive_output() and rows:
        return source_search(rows)
    render_sources_table(rows)
    return EXIT_OK


async def _probe_estimate_display(
    images: list,
    referer_url: str,
    quiet: bool,
    *,
    known_size: int = 0,
) -> None:
    """Print a best-effort size estimate for display only.

    Never gates or blocks the real download: failures, timeouts and unknown
    sizes all silently fall through to a no-op.

    When the site reports an exact size (e.g. e-hentai's gdata ``filesize``)
    that value is used directly and the probe is skipped — there is no point
    burning up to the probe budget re-measuring what the source already knows.
    Otherwise the line appears only when ``probe_download_size`` returns a
    positive value, which requires at least three successful ``content-length``
    / ``content-range`` probes on a sample of pages within the budget — sites
    without size headers (or with slow responses) print nothing.
    """
    if quiet or not images:
        return
    if known_size and known_size > 0:
        vlog(VERBOSE, f"Estimated download size: ~{format_bytes(known_size)}")
        return
    try:
        estimate = await asyncio.wait_for(
            probe_download_size(images, referer_url),
            timeout=5.0,
        )
    except Exception:
        return
    if estimate > 0:
        vlog(VERBOSE, f"Estimated download size: ~{format_bytes(estimate)}")


@dataclass(slots=True)
class DownloadStats:
    """Machine-readable summary of one URL processed by ``process_url``.

    Populated by ``process_url``/``_process_series`` and consumed by
    ``_run_urls`` for ``--json`` output. ``error``/``message`` are set only
    when the run failed before any status could be reported.
    """

    status: str = "failed"
    output_path: str = ""
    chapters_downloaded: int = 0
    chapters_partial: int = 0
    bytes: int = 0
    error: int = 0
    message: str = ""
    missing_pages: int = 0
    total_pages: int = 0


def _url_result(url: str, stats: DownloadStats, duration_s: float) -> dict:
    """JSON object describing one URL for ``--json`` download output."""
    result: dict[str, object] = {
        "url": url,
        "status": stats.status,
        "output_path": stats.output_path or None,
        "chapters_downloaded": stats.chapters_downloaded,
        "bytes": stats.bytes,
        "duration_s": round(duration_s, 3),
    }
    if stats.error:
        result["error"] = stats.error
    if stats.message:
        result["message"] = stats.message
    if stats.missing_pages or stats.total_pages:
        result["missing_pages"] = stats.missing_pages
        result["total_pages"] = stats.total_pages
    return result


async def _run_with_network_retry(
    run_once: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 3,
    quiet: bool = False,
) -> Any:
    """Run ``run_once()``, retrying the whole unit on transient network
    failures.

    The metadata fetch already retries transient errors internally, but the
    streaming download phase (image-URL resolution via ``iter_images``) had no
    such safety net — a flaky connection there escaped every retry and failed
    the chapter (a common failure on slow e-hentai galleries).
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await run_once()
        except (
            ScrapeTimeout,
            CurlConnectionError,
            CurlTimeout,
            ConnectionError,
            TimeoutError,
        ) as exc:
            last_error = exc
            if attempt >= attempts - 1:
                break
            if not quiet:
                print_retry(attempt + 2, attempts, reason="network error")
            await asyncio.sleep((1.0 + attempt) * 0.8)
    if last_error is None:  # pragma: no cover - the loop always sets it
        raise RuntimeError("retry loop exhausted without recording an error")
    raise last_error


@dataclass(frozen=True)
class _ChapterUrlGuard:
    """Per-domain chapter-URL gate: shape check + optional normalization."""

    check: Any  # Callable[[str], bool]
    expected: str
    normalize: Any = None  # Optional[Callable[[str], str]]


# Table-driven gates keep the per-domain messaging uniform: one lookup here
# instead of six near-identical if-blocks drifting apart over time.
_CHAPTER_URL_GUARDS: dict[str, _ChapterUrlGuard] = {
    "webtoons.com": _ChapterUrlGuard(
        is_webtoon_chapter_url,
        "https://www.webtoons.com/...?title_no=<n>&episode_no=<n>",
        normalize_webtoon_url,
    ),
    "flamecomics.xyz": _ChapterUrlGuard(
        is_flamecomics_chapter_url,
        "https://flamecomics.xyz/series/{id}/{chapter_token}",
    ),
    "fsicomics.com": _ChapterUrlGuard(
        is_fsicomics_chapter_url,
        "https://fsicomics.com/{comic-slug}/",
    ),
    "gedecomix.com": _ChapterUrlGuard(
        is_gedecomix_chapter_url,
        "https://gedecomix.com/porncomic/{series}/{chapter}/",
    ),
    "asurascans.com": _ChapterUrlGuard(
        is_asurascans_chapter_url,
        "https://asurascans.com/comics/{series}/chapter/{n} "
        "or https://asurascans.com/comics/{series}/",
    ),
    "kagane.to": _ChapterUrlGuard(
        is_kagane_chapter_url,
        "https://kagane.to/series/{series}/reader/{book} "
        "or https://kagane.to/series/{series}/",
    ),
}


async def process_url(
    url: str,
    output_dir: Path,
    concurrency: int,
    force: bool,
    max_image_size: int = 100 * 1024 * 1024,
    max_total_size: int = 0,
    quiet: bool = False,
    chapters: str | None = None,
    interactive: bool = False,
    library: Library | None = None,
    stats: DownloadStats | None = None,
    label: str = "",
    activity: Activity | None = None,
    row_key: str = "",
    chapter_parallel: int = 1,
    compression: str = "stored",
    fmt: str = "cbz",
    failure_sink: list[tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """Download one URL (or resolve it to a series/chapter) and return stats.

    Returns the ``(output_path, error_or_empty)`` pair. Handles both single
    chapters and whole series, applies retries, updates the library, and
    reports progress through ``activity``/``stats``.

    ``failure_sink`` collects ``(url, reason)`` pairs instead of printing the
    per-URL error inline, so a batch can defer and group its failures into one
    recap instead of interleaving duplicate lines with the live rows.
    """
    if stop_requested():
        return "interrupted", ""
    url = normalize_url(url)

    def _fail(reason: str) -> tuple[str, str]:
        """Record or print a failure, returning the ``("failed", "")`` pair."""
        if failure_sink is not None:
            failure_sink.append((url, reason))
        else:
            print_error(reason)
        return "failed", ""

    domain = _extract_domain(url)
    vlog(VERBOSE, f"Source: {domain}")
    trace(f"dispatch: host → {domain or '<none>'}")

    series_scraper = get_series_scraper(domain)
    # The same checkers drive the dry-run preview path via _SERIES_URL_CHECKERS.
    series_check = (
        _SERIES_URL_CHECKERS.get(domain)
        if series_scraper is not None
        else None
    )
    if series_check is not None and series_check(url):
        trace(f"dispatch: {domain} → series mode ({type(series_scraper).__name__})")
        ok = await _process_series(
            scraper=series_scraper,
            url=url,
            output_dir=output_dir,
            concurrency=concurrency,
            force=force,
            max_image_size=max_image_size,
            max_total_size=max_total_size,
            quiet=quiet,
            chapters_spec=chapters,
            interactive=interactive,
            stats=stats,
            label=label,
            activity=activity,
            row_key=row_key,
            chapter_parallel=chapter_parallel,
            library=library,
            compression=compression,
            fmt=fmt,
        )
        return ("downloaded", "") if ok else ("failed", "")

    guard = _CHAPTER_URL_GUARDS.get(domain)
    if guard is not None:
        if not guard.check(url):
            return _fail(
                f"Unsupported URL for domain '{domain}'. "
                f"Expected format: {guard.expected}"
            )
        if guard.normalize is not None:
            url = guard.normalize(url)

    scraper = get_chapter_scraper(domain)
    if not scraper and generic_enabled():
        generic = get_generic_scraper()
        if generic is not None:
            if domain:
                print_dim(f"Using generic extraction for {domain} {glyphs().ellipsis}")
            async with AsyncSession(**_with_referer(url)) as client:
                try:
                    kind = await generic.detect(url, client)
                except (CurlHTTPError, CurlConnectionError, CurlTimeout, ScrapeTimeout):
                    trace("dispatch: generic probe failed (network error)")
                    kind = None
                except Exception as exc:
                    report_error(
                        exc,
                        context="Generic extraction failed",
                        hint="Run again with -vvv for a traceback.",
                    )
                    kind = None
            if kind == "series":
                trace("dispatch: generic → series mode")
                ok = await _process_series(
                    scraper=generic,
                    url=url,
                    output_dir=output_dir,
                    concurrency=concurrency,
                    force=force,
                    max_image_size=max_image_size,
                    max_total_size=max_total_size,
                    quiet=quiet,
                    chapters_spec=chapters,
                    interactive=interactive,
                    stats=stats,
                    label=label,
                    activity=activity,
                    row_key=row_key,
                    chapter_parallel=chapter_parallel,
                    library=library,
                    compression=compression,
                    fmt=fmt,
                )
                return ("downloaded", "") if ok else ("failed", "")
            if kind == "gallery":
                trace("dispatch: generic → gallery")
                scraper = generic
    if not scraper:
        reason = f"Unsupported URL for domain '{domain}'." if domain else "Unsupported URL."
        if failure_sink is not None:
            failure_sink.append((url, reason))
        else:
            print_error(reason)
        _print_supported_sources()
        return "failed", ""
    trace(f"dispatch: {domain} → chapter scraper {type(scraper).__name__}")

    async with _chapter_activity(
        activity, row_key, quiet=quiet, label=label, url=url,
    ) as main:
        async with AsyncSession(**_with_referer(url)) as client:
            stream_mode = bool(getattr(scraper, "streaming_images", False))
            last_error: Exception | None = None
            meta: PostMetadata | None = None
            for attempt in range(3):
                try:
                    main.stage("Fetching chapter...")
                    if attempt:
                        main.set_activity(
                            f"waiting for server{glyphs().ellipsis} retry {attempt + 1}/3"
                        )
                    if stream_mode:
                        meta = await scraper.scrape_meta(url, client)
                    else:
                        meta = await scraper.scrape(url, client)
                    last_error = None
                    break
                except CurlHTTPError as e:
                    code = e.response.status_code if e.response else 0
                    if code in (429, 500, 502, 503, 504) and attempt < 2:
                        if not quiet:
                            print_retry(attempt + 2, 3, reason=f"HTTP {code}")
                        last_error = e
                        await asyncio.sleep(_scrape_retry_sleep(e, attempt))
                        continue
                    return _fail(
                        "Failed to fetch metadata: "
                        + _HTTP_ERROR_REASONS.get(
                            code,
                            f"HTTP {code} — may not exist or requires login.",
                        )
                    )
                except (CurlConnectionError, CurlTimeout) as e:
                    if attempt < 2:
                        if not quiet:
                            print_retry(attempt + 2, 3, reason="connection error")
                        last_error = e
                        await asyncio.sleep((1.0 + attempt) * 0.5)
                        continue
                    return _fail(
                        "Could not connect to the server. Check your internet connection."
                    )
                except ScrapeTimeout as e:
                    if attempt < 2:
                        if not quiet:
                            print_retry(attempt + 2, 3, reason=f"timed out after {e.timeout:.0f}s")
                        last_error = e
                        await asyncio.sleep((1.0 + attempt) * 0.5)
                        continue
                    return _fail(f"Failed to fetch metadata: {e}")
                except ValueError as e:
                    return _fail(f"Failed to process gallery: {e}")
                except Exception as e:
                    if VERBOSITY >= TRACE:
                        import traceback

                        traceback.print_exception(e)
                    return _fail(f"Failed to process gallery: {_classify(e)[0]}")

            if last_error is not None or meta is None:
                return _fail("Failed to fetch metadata after retries.")

            if activity is None:
                # Standalone runs would otherwise render an unlabeled spinner;
                # attribute the row to the chapter once its title is known.
                main.set_label(meta.chapter_title)

        main.stage("Parsing chapter info...")

        text_content = getattr(meta, "text_content", None)
        if text_content:
            series_dir = ensure_unique_dir(output_dir, meta.series_title)
            _ensure_nomedia(output_dir)
            md_name = sanitize_filename(meta.chapter_title) + ".md"
            md_path = series_dir / md_name
            if md_path.exists() and not force:
                print_skipped(f"Skipped: {md_name}")
                return "skipped", ""
            md_path.write_text(
                f"{_TEXT_SOURCE_PREFIX}{normalize_url(url)}{_TEXT_SOURCE_SUFFIX}\n"
                f"# {meta.chapter_title}\n\n{text_content}\n",
                encoding="utf-8",
            )
            print_warning(
                "This post is text-only (no images). "
                f"Saved the post content as {md_name}."
            )
            if library is not None:
                with contextlib.suppress(ValueError):
                    library.upsert_download(
                        normalize_url(url),
                        md_path.relative_to(output_dir).as_posix(),
                        "md",
                    )
            if not quiet and activity is None:
                print_meta("Series", meta.series_title)
                print_meta("Chapter", meta.chapter_title)
            if stats is not None:
                stats.output_path = str(md_path)
                stats.chapters_downloaded = 1
                with contextlib.suppress(OSError):
                    stats.bytes = md_path.stat().st_size
            return "downloaded", md_name

        if (
            not stream_mode
            and meta.total_pages is not None
            and len(meta.images) != meta.total_pages
        ):
            print_warning(
                f"Expected {meta.total_pages} page(s), extracted {len(meta.images)}"
            )

        for w in meta.warnings:
            print_warning(w)

        series_dir = ensure_unique_dir(output_dir, meta.series_title)
        _ensure_nomedia(output_dir)
        vlog(VERBOSE, f"Output: {series_dir}")
        if meta.cover_url:
            await download_cover_to(
                meta.cover_url,
                series_dir / "cover.jpg",
                referer_url=url,
                force=force,
            )

        _write_series_comicinfo(
            series_dir,
            series_title=meta.series_title,
            source_url=url,
            description=meta.description,
            meta=meta,
        )

        cbz_path = _resolve_archive_path(
            series_dir, meta.chapter_title, url, meta.post_id, force, quiet, fmt,
        )
        if cbz_path is None:
            return "skipped", ""

        if activity is not None:
            # Shared batch: keep attribution inside the row label so metadata
            # lines never interleave under the wrong URL's header.
            main.set_label(
                f"{meta.series_title} — {meta.chapter_title} "
                f"({meta.total_pages or len(meta.images)}p)"
            )
        elif not quiet:
            print_meta("Series", meta.series_title)
            print_meta("Chapter", meta.chapter_title)
            print_meta("Pages", str(meta.total_pages or len(meta.images)))

        num_pages = meta.total_pages or len(meta.images)
        if max_total_size > 0:
            estimate = _estimate_download_bytes(meta.estimated_size)
            # Prefer the site-provided estimate when known so a big gallery
            # with small pages isn't rejected on a guess. When unknown,
            # guess from a realistic per-page size instead of the worst-case
            # pages*cap product (200p x 100MB = 20GB rejected modest runs);
            # the runtime total-size cap still enforces --max-size exactly.
            if estimate > 0:
                estimated_max = estimate
                basis = "site-reported size"
            else:
                estimated_max = num_pages * UNKNOWN_PAGE_BYTES_GUESS
                basis = "page-count estimate"
            if estimated_max > max_total_size:
                return _fail(
                    f"Estimated download size ({estimated_max / 1024 / 1024:.0f} MB, "
                    f"{basis}) exceeds --max-size "
                    f"({max_total_size / 1024 / 1024:.0f} MB)."
                )

        tmp_dir = _tmp_root() / sanitize_filename(f"{meta.series_title}_{meta.chapter_title}")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        if not force and _is_partial(cbz_path):
            # Seed the temp dir with the previous run's intact pages so the
            # skip-if-present check spares them; only gaps hit the network.
            _restore_pages_from_archive(cbz_path, tmp_dir)

        total = meta.total_pages or len(meta.images)

        main.stage("Estimating download size...")
        if activity is None and not stream_mode:
            # The probe is display-only, so overlap it with the local disk
            # check + tmp-dir setup instead of stalling the download on it.
            probe_task = asyncio.create_task(
                _probe_estimate_display(
                    meta.images, url, quiet, known_size=meta.estimated_size
                )
            )
        else:
            probe_task = None

        estimate = _estimate_download_bytes(meta.estimated_size)
        ok = _check_disk_space(series_dir, estimate)
        if probe_task is not None:
            await probe_task
        if not ok:
            return "failed", ""

        if max_image_size and max_image_size < 1024 * 1024:
            print_warning("max-image-size is under 1 MB — images may be rejected")

        pipeline_start = time.monotonic()
        if stream_mode:
            async with AsyncSession(**_with_referer(url)) as stream_client:
                async def _run_stream_once() -> Any:
                    return await DownloadPipeline(
                        images=[],
                        images_iter=scraper.iter_images(url, stream_client, total_pages=total),
                        total_pages=total,
                        tmp_dir=tmp_dir,
                        cbz_path=cbz_path,
                        series_title=meta.series_title,
                        chapter_title=meta.chapter_title,
                        url=url,
                        chapter_number=meta.chapter_number,
                        volume_number=meta.volume_number,
                        series_meta=meta,
                        concurrency=concurrency,
                        max_image_size=max_image_size,
                        max_total_size=max_total_size,
                        referer_url=url,
                        quiet=quiet,
                        status_sink=main,
                        client=stream_client,
                        compression=compression,
                    ).run()

                result = await _run_with_network_retry(_run_stream_once, quiet=quiet)
        else:
            result = await DownloadPipeline(
                images=meta.images,
                tmp_dir=tmp_dir,
                cbz_path=cbz_path,
                series_title=meta.series_title,
                chapter_title=meta.chapter_title,
                url=url,
                chapter_number=meta.chapter_number,
                volume_number=meta.volume_number,
                series_meta=meta,
                concurrency=concurrency,
                max_image_size=max_image_size,
                max_total_size=max_total_size,
                referer_url=url,
                quiet=quiet,
                status_sink=main,
                compression=compression,
            ).run()
        vlog(
            DIAGNOSTIC,
            f"{meta.chapter_title}: {time.monotonic() - pipeline_start:.1f}s",
            tag=TAG_TIMING,
        )
        vlog(
            DIAGNOSTIC,
            f"{meta.chapter_title}: downloaded "
            f"{total - len(result.failed_images)}/{total} images",
            tag=TAG_DOWNLOAD,
        )

        if result.ok:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            if result.failed_images:
                # Partial chapter: keep the CBZ but mark it incomplete so a
                # rerun retries the missing pages instead of skipping it.
                _partial_marker(cbz_path).touch(exist_ok=True)
                vlog(
                    DIAGNOSTIC,
                    f"Partial download: {len(result.failed_images)} of "
                    f"{total} pages failed. Rerun to retry the missing pages.",
                    tag=TAG_DOWNLOAD,
                )
                if stats is not None:
                    stats.output_path = str(cbz_path)
                    stats.chapters_downloaded = 1
                    stats.bytes = result.cbz_size
                    stats.missing_pages = len(result.failed_images)
                    stats.total_pages = total
                return "partial", cbz_path.name
            _partial_marker(cbz_path).unlink(missing_ok=True)
            if library is not None:
                with contextlib.suppress(ValueError):
                    library.upsert_download(
                        normalize_url(url),
                        cbz_path.relative_to(output_dir).as_posix(),
                        fmt,
                    )
            if stats is not None:
                stats.output_path = str(cbz_path)
                stats.chapters_downloaded = 1
                stats.bytes = result.cbz_size
            return "downloaded", cbz_path.name

        return "failed", ""


async def _process_series(
    scraper,
    url: str,
    output_dir: Path,
    concurrency: int,
    force: bool,
    max_image_size: int = 100 * 1024 * 1024,
    max_total_size: int = 0,
    quiet: bool = False,
    chapters_spec: str | None = None,
    interactive: bool = False,
    stats: DownloadStats | None = None,
    label: str = "",
    activity: Activity | None = None,
    row_key: str = "",
    chapter_parallel: int = 1,
    library: Library | None = None,
    compression: str = "stored",
    fmt: str = "cbz",
) -> bool:
    if activity is None and not quiet:
        err_console.print()
        err_console.print(Rule(style="dim"))
        if label:
            print_header(f"[{label}] Processing series")
        else:
            print_header("Processing series")
        print_url(url)

    owns_library = library is None or not library.available
    if library is None or not library.available:
        library = _open_library(output_dir)
    if library is None:
        return False

    try:
        if activity is not None:
            act = activity
            main_key, chapters_key = f"{row_key}:main", f"{row_key}:chapters"
        else:
            act = Activity(quiet=quiet)
            main_key, chapters_key = "main", "chapters"
        async with (act if activity is None else contextlib.nullcontext()):
            main = act.row(main_key)

            session_kwargs = {**_with_referer(url)}
            session_kwargs["max_clients"] = min(
                chapter_parallel * (concurrency * (concurrency + 1) + 8),
                MAX_CLIENTS,
            )

            async with AsyncSession(**session_kwargs) as client:
                series_info = None
                series_scrape_error: Exception | None = None
                for series_attempt in range(3):
                    try:
                        main.stage("Fetching series metadata...")
                        if series_attempt:
                            main.set_activity(
                                f"waiting for server{glyphs().ellipsis} "
                                f"retry {series_attempt + 1}/3"
                            )
                        series_info = await scraper.scrape_series(url, client)
                        break
                    except CurlHTTPError as e:
                        code = e.response.status_code if e.response else 0
                        if code in (429, 500, 502, 503, 504) and series_attempt < 2:
                            if not quiet:
                                print_retry(series_attempt + 2, 3, reason=f"HTTP {code}")
                            series_scrape_error = e
                            await asyncio.sleep(_scrape_retry_sleep(e, series_attempt))
                            continue
                        series_scrape_error = e
                        break
                    except (CurlConnectionError, TimeoutError, ScrapeTimeout) as e:
                        if series_attempt < 2:
                            if not quiet:
                                reason = (
                                    f"timed out after {e.timeout:.0f}s"
                                    if isinstance(e, ScrapeTimeout)
                                    else "connection/timeout"
                                )
                                print_retry(series_attempt + 2, 3, reason=reason)
                            series_scrape_error = e
                            await asyncio.sleep((1.0 + series_attempt) * 0.5)
                            continue
                        series_scrape_error = e
                        break
                    except Exception as e:
                        series_scrape_error = e
                        break

                if activity is None:
                    main.set_label("")
                else:
                    main.set_label(label or row_key)

                if series_info is None:
                    default = str(series_scrape_error or "unknown error")
                    if isinstance(series_scrape_error, CurlHTTPError):
                        reason = _HTTP_ERROR_REASONS.get(
                            series_scrape_error.response.status_code
                            if series_scrape_error.response
                            else 0,
                            default,
                        )
                    else:
                        reason = default
                    print_error_detail("Failed to fetch series metadata", reason)
                    return False

                main.stage("Parsing series info...")
                series_title = series_info.series_title
                description = series_info.description
                cover_url = series_info.cover_url
                chapters = sorted(
                    series_info.chapters,
                    key=_chapter_order_key,
                )
                total_chapters = len(chapters)
                domain = _extract_domain(url)
                vlog(VERBOSE, f"Source: {domain}")
                vlog(DIAGNOSTIC, f"found {total_chapters} chapters", tag=TAG_SCRAPE)

                title_no = getattr(series_info, "title_no", "") or ""
                series_id = f"{domain}:{title_no}" if title_no else normalize_url(url)
                # Path is computed without creating the directory so that a
                # cancelled selection (q / Esc) leaves no filesystem trace.
                series_dir = output_dir / sanitize_filename(series_title)

                have_urls: set[str] = set()
                new_items: list[tuple[int, dict]] = []
                if force:
                    new_items = list(enumerate(chapters, start=1))
                else:
                    main.stage("Checking downloaded chapters...")
                    have_urls = await asyncio.to_thread(
                        library.build_have_set, series_id, series_dir, chapters,
                    )
                    for idx, ch in enumerate(chapters, start=1):
                        if normalize_url_key(ch.get("url") or "") in have_urls:
                            continue
                        new_items.append((idx, ch))

                # One new chapter: skip the preview table and the interactive
                # picker and download it directly (unless verbose). The picker
                # exists to choose among several chapters; a single new chapter
                # has nothing to choose.
                single_new = len(new_items) == 1 and not force
                verbose = VERBOSITY >= VERBOSE
                if not quiet and activity is None and not (single_new and not verbose):
                    print_chapter_preview(
                        chapters, total_chapters, series_title, have_urls=have_urls,
                    )
                elif single_new and not quiet and activity is None:
                    ch = new_items[0][1]
                    label = ch.get("title") or ch.get("episode_no") or "1"
                    print_dim(f"New: {label}")

                selection: ChapterSelection
                if chapters_spec is not None:
                    selection = parse_chapter_selection(chapters_spec, total_chapters)
                elif interactive and not (single_new and not verbose):
                    act.pause()
                    try:
                        selected = _prompt_chapter_selection(
                            chapters, series_title, have_urls,
                        )
                    finally:
                        act.resume()
                    selection = (
                        ChapterSelection(kind="quit")
                        if selected is None
                        else ChapterSelection(
                            kind="indices", indices=frozenset(selected)
                        )
                    )
                else:
                    selection = ChapterSelection(kind="all")

                if selection.kind == "quit":
                    raise ChapterSelectionQuit
                if selection.kind == "indices":
                    selected_numbers = selection.indices or frozenset()
                    new_items = [
                        it for it in new_items if it[0] in selected_numbers
                    ]
                    if not quiet and activity is None:
                        sel = len(selected_numbers)
                        word = "chapter" if sel == 1 else "chapters"
                        print_dim(f"Selected {sel}/{total_chapters} {word}")

                if not new_items:
                    # A cancelled selection (q / Esc / empty) must leave no
                    # trace, so only touch an existing series directory.
                    if series_dir.exists() and not (series_dir / "ComicInfo.xml").exists():
                        _write_series_comicinfo(
                            series_dir,
                            series_title=series_title,
                            source_url=url,
                            description=description,
                            meta=None,
                        )
                    library.set_last_checked(series_id)
                    if stats is not None:
                        stats.output_path = str(series_dir)
                    return True

                series_dir = ensure_unique_dir(output_dir, series_title)
                _ensure_nomedia(output_dir)
                vlog(VERBOSE, f"Output: {series_dir}")
                if cover_url:
                    await download_cover_to(
                        cover_url,
                        series_dir / "cover.jpg",
                        client=client,
                        referer_url=url,
                        force=force,
                    )

                if library.available:
                    relative_path = ""
                    with contextlib.suppress(ValueError):
                        relative_path = series_dir.resolve().relative_to(
                            output_dir.resolve()
                        ).as_posix()
                    library.upsert_series(
                        series_id,
                        title=series_title,
                        source=normalize_url(url),
                        source_site=domain,
                        relative_path=relative_path,
                        source_id=_source_id_for(domain),
                    )
                downloaded = 0
                skipped = total_chapters - len(new_items)
                failed_count = 0
                partial_count = 0
                interrupted = False
                failures: list[tuple[str, str]] = []
                partial_failures: list[tuple[str, str]] = []
                total_bytes = 0
                start_time = time.monotonic()

                act.remove_row(main_key)
                # Batch Overall header: pre-create every chapter row as queued
                # so the count is complete before any task starts; each task
                # flips its own row to running as it begins and retires it via
                # finish_row, which feeds the Overall + completed sections.
                act.begin_batch(len(new_items))
                for _idx, _ch in new_items:
                    _ch_title = _ch["title"]
                    _ch_label = _ch_title or f"Ep. {_ch['episode_no']}"
                    act.add_queued_row(
                        f"{chapters_key}:{_idx}",
                        label=f"{_ch_label}  ({_idx}/{total_chapters})",
                    )
                # Up to ``chapter_parallel`` chapters run at once, each on its
                # own Activity row; the semaphore keeps the concurrency bounded.
                last_meta_by_idx: dict[int, object] = {}
                chapter_sem = asyncio.Semaphore(chapter_parallel)

                async def _process_one_chapter(idx: int, ch: dict) -> tuple[str, int, object]:
                    if stop_requested():
                        return "interrupted", 0, None
                    row_key = f"{chapters_key}:{idx}"
                    act.mark_running(row_key, stage="Preparing chapter...")
                    sink = act.row(row_key)
                    ch_url = ch["url"]
                    ch_title = ch["title"]
                    ch_label = ch_title or f"Ep. {ch['episode_no']}"

                    sink.set_label(f"{ch_label}  ({idx}/{total_chapters})")
                    sink.stage("Preparing chapter...")

                    ch_slug = ch_title or ch["episode_no"]
                    tmp_dir = _tmp_root() / sanitize_filename(
                        f"{series_title}_{ch_slug}"
                    )
                    tmp_dir.mkdir(parents=True, exist_ok=True)

                    # Brief randomized pause to keep requests spread out over time.
                    # Only the sequential path sleeps — parallel chapters already
                    # interleave naturally.
                    if chapter_parallel == 1:
                        sink.stage(f"Pacing before chapter {ch['episode_no']}...")
                        await asyncio.sleep(max(0.3, min(4.0, random.gauss(1.5, 0.5))))

                    async with chapter_sem:
                        meta = None
                        scrape_error: str | None = None
                        for attempt in range(3):
                            sink.stage(
                                f"Fetching chapter {ch['episode_no']} metadata..."
                            )
                            if attempt:
                                sink.set_activity(
                                f"waiting for server{glyphs().ellipsis} retry {attempt + 1}/3"
                            )
                            try:
                                meta = await scraper.scrape(ch_url, client)
                                break
                            except CurlHTTPError as e:
                                code = e.response.status_code if e.response else 0
                                if code in (429, 500, 502, 503, 504) and attempt < 2:
                                    if not quiet:
                                        print_retry(attempt + 2, 3, reason=f"HTTP {code}")
                                    await asyncio.sleep((1.0 + attempt) * 0.5)
                                    continue
                                scrape_error = _HTTP_ERROR_REASONS.get(
                                    code,
                                    f"HTTP {code} — may not exist or requires login.",
                                )
                                print_error_detail(
                                    f"[{idx}/{total_chapters}] {ch_label}", scrape_error
                                )
                                break
                            except (CurlConnectionError, CurlTimeout, ScrapeTimeout) as e:
                                if attempt < 2:
                                    if not quiet:
                                        reason = (
                                            f"timed out after {e.timeout:.0f}s"
                                            if isinstance(e, ScrapeTimeout)
                                            else "connection error"
                                        )
                                        print_retry(attempt + 2, 3, reason=reason)
                                    await asyncio.sleep((1.0 + attempt) * 0.5)
                                    continue
                                print_error_detail(
                                    f"[{idx}/{total_chapters}] {ch_label}", "connection failed"
                                )
                                break
                            except ValueError as e:
                                print_error_detail(f"[{idx}/{total_chapters}] {ch_label}", str(e))
                                break
                            except RequestBlockedError as e:
                                print_error_detail(
                                    f"[{idx}/{total_chapters}] {ch_label}", str(e)
                                )
                                break

                        if meta is None:
                            fail_reason = scrape_error or "failed to fetch metadata"
                            act.finish_row(row_key, ok=False, message=fail_reason)
                            return "failed", 1, (ch_label, fail_reason)

                        sink.set_label(f"{ch_label}  ({idx}/{total_chapters})")

                        total_pages = len(meta.images)
                        if total_pages == 0:
                            act.finish_row(row_key, ok=False, message="no images found")
                            print_error_detail(
                                f"[{idx}/{total_chapters}] {ch_label}", "no images found"
                            )
                            return "failed", 1, (ch_label, "no images found")

                        cbz_path = _resolve_archive_path(
                            series_dir, ch_label, ch_url, meta.post_id, force, quiet,
                            fmt,
                        )
                        if cbz_path is None:
                            act.remove_row(row_key)
                            return "skipped", 0, 0

                        if not force and _is_partial(cbz_path):
                            # Seed the temp dir with the previous run's
                            # intact pages so only gaps hit the network.
                            _restore_pages_from_archive(cbz_path, tmp_dir)

                        estimate = _estimate_download_bytes(meta.estimated_size)
                        ok = _check_disk_space(series_dir, estimate)
                        if not ok:
                            act.finish_row(
                                row_key, ok=False, message="insufficient disk space"
                            )
                            return "failed", 1, (ch_label, "insufficient disk space")

                        pipeline_start = time.monotonic()
                        result = await DownloadPipeline(
                            images=meta.images,
                            tmp_dir=tmp_dir,
                            cbz_path=cbz_path,
                            series_title=series_title,
                            chapter_title=ch_label,
                            url=ch_url,
                            chapter_number=meta.chapter_number,
                            volume_number=meta.volume_number,
                            series_meta=meta,
                            concurrency=concurrency,
                            max_image_size=max_image_size,
                            max_total_size=max_total_size,
                            referer_url=ch_url,
                            quiet=quiet,
                            client=client,
                            status_sink=sink,
                            compression=compression,
                        ).run(series_prefix=f"[{idx}/{total_chapters}] ")
                        vlog(
                            DIAGNOSTIC,
                            f"Chapter {ch['episode_no']}: "
                            f"{time.monotonic() - pipeline_start:.1f}s",
                            tag=TAG_TIMING,
                        )
                        vlog(
                            DIAGNOSTIC,
                            f"Chapter {ch['episode_no']}: "
                            f"{total_pages - len(result.failed_images)}/{total_pages} images",
                            tag=TAG_DOWNLOAD,
                        )

                        if result.ok:
                            if result.failed_images:
                                # Partial chapter: keep the CBZ but mark it
                                # incomplete so a rerun retries the missing
                                # pages instead of skipping it.
                                shutil.rmtree(tmp_dir, ignore_errors=True)
                                _partial_marker(cbz_path).touch(exist_ok=True)
                                library.upsert_chapter(
                                    series_id,
                                    url=ch_url,
                                    chapter_id=getattr(meta, "post_id", "") or "",
                                    chapter_no=getattr(meta, "chapter_number", None),
                                    title=ch_label,
                                    cbz=result.cbz_path.name,
                                    size_bytes=result.cbz_size,
                                    page_count=result.cbz_pages,
                                )
                                message = (
                                    f"{len(result.failed_images)}/{total_pages} "
                                    "pages missing"
                                )
                                return "partial", result.cbz_size, (ch_label, message)
                            last_meta_by_idx[idx] = meta
                            shutil.rmtree(tmp_dir, ignore_errors=True)
                            _partial_marker(cbz_path).unlink(missing_ok=True)
                            library.upsert_chapter(
                                series_id,
                                url=ch_url,
                                chapter_id=getattr(meta, "post_id", "") or "",
                                chapter_no=getattr(meta, "chapter_number", None),
                                title=ch_label,
                                cbz=result.cbz_path.name,
                                size_bytes=result.cbz_size,
                                page_count=result.cbz_pages,
                            )
                            return "downloaded", result.cbz_size, result.cbz_path.name
                        else:
                            return "failed", 1, (ch_label, result.error)

                results_by_idx: dict[int, tuple[str, int, object]] = {}

                async def _run_chapter(idx: int, ch: dict) -> None:
                    results_by_idx[idx] = await _process_one_chapter(idx, ch)

                await asyncio.gather(
                    *(_run_chapter(idx, ch) for idx, ch in new_items)
                )

                for idx, _ch in new_items:
                    status, count, payload = results_by_idx[idx]
                    if status == "interrupted":
                        # Graceful stop: in-flight chapter finished, no
                        # new ones will start.  Skip recording — the
                        # caller handles the exit code.
                        interrupted = True
                        continue
                    elif status == "skipped":
                        skipped += 1
                    elif status == "partial":
                        # Chapter saved but missing pages: NOT a failure — a
                        # distinct, retryable outcome (the .partial marker
                        # makes a rerun resume it).
                        partial_count += 1
                        total_bytes += count
                        partial_failures.append(cast(tuple[str, str], payload))
                    elif status == "failed":
                        failed_count += 1
                        failures.append(cast(tuple[str, str], payload))
                    elif status == "downloaded":
                        downloaded += 1
                        total_bytes += count

                last_meta = None
                for idx in sorted(last_meta_by_idx):
                    last_meta = last_meta_by_idx[idx]

                if downloaded > 0:
                    library.set_last_updated(series_id)
                library.set_last_checked(series_id)

                _write_series_comicinfo(
                    series_dir,
                    series_title=series_title,
                    source_url=url,
                    description=description,
                    meta=last_meta,
                )

            main = act.row(main_key)
            main.stage("Writing series summary...")
            elapsed_secs = time.monotonic() - start_time
        if elapsed_secs >= 60:
            elapsed_str = f"{int(elapsed_secs // 60)}m {int(elapsed_secs % 60)}s"
        else:
            elapsed_str = f"{int(elapsed_secs)}s"

        if failures:
            print_failure_recap(failures)
        if partial_failures:
            print_partial_recap(partial_failures)
        if not quiet:
            print_summary(
                series_title=series_title,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed_count,
                partial=partial_count,
                output_dir=str(series_dir),
                elapsed=elapsed_str,
                total_bytes=total_bytes,
                elapsed_secs=elapsed_secs,
                interrupted=interrupted,
            )

        if stats is not None:
            stats.output_path = str(series_dir)
            stats.chapters_downloaded = downloaded
            stats.chapters_partial = partial_count
            stats.bytes = total_bytes

        return failed_count == 0 and partial_count == 0
    finally:
        if owns_library:
            library.close()


_WORK_TASK: asyncio.Task | None = None
_STOP_REQUESTED: bool = False
_STOP_PRESS_TIME: float = 0.0
INTERRUPT_GRACE_SECS: float = 2.0
_RESUME_CMD: str = ""


def stop_requested() -> bool:
    """Return ``True`` when a SIGINT/SIGTERM has requested a graceful stop."""
    return _STOP_REQUESTED


def request_stop() -> None:
    """Set the stop flag so download loops finish the current item and unwind."""
    global _STOP_REQUESTED, _STOP_PRESS_TIME
    _STOP_REQUESTED = True
    _STOP_PRESS_TIME = time.monotonic()


def reset_stop() -> None:
    """Clear the stop flag at the start of a new run."""
    global _STOP_REQUESTED, _STOP_PRESS_TIME, _RESUME_CMD
    _STOP_REQUESTED = False
    _STOP_PRESS_TIME = 0.0
    _RESUME_CMD = ""


def _force_exit_130() -> None:
    """Hard-exit path: teardown live UI, flush debug, clean temp, print interrupt, exit 130."""
    progress = active_snapshot()
    teardown_active()
    flush_debug_file()
    _cleanup_temp_dir()
    err_console.print()
    partial = bool(active_partial_files())
    resume_cmd = _RESUME_CMD or ""
    print_interrupt(
        progress if partial else "",
        partial=partial,
        resume_cmd=resume_cmd,
    )
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(130)


def _handle_interrupt(signum: int, frame: object) -> None:
    if _WORK_TASK is None:
        raise KeyboardInterrupt

    # Second press within the grace window → force exit immediately.
    if _STOP_REQUESTED:
        elapsed = time.monotonic() - _STOP_PRESS_TIME
        if elapsed <= INTERRUPT_GRACE_SECS:
            _force_exit_130()
        # Outside the grace window: treat this as a fresh first press.

    # First press (or grace-window-expired second press): request a graceful
    # stop.  The download loops check ``stop_requested()`` at item boundaries
    # and unwind once the current item finishes.
    request_stop()

    # Wake the event loop so the worker notices without busy-polling.
    try:
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(lambda: None)
    except RuntimeError:
        pass


_SECRET_PATTERNS = re.compile(
    r"(cookie|token|key|secret|password|pass|auth|credential)",
    re.IGNORECASE,
)


def resume_command(argv: list[str] | None = None, *, url: str = "", output: str = "") -> str:
    """Reconstruct the command the user ran, for the resume hint.

    Secrets (cookie values, tokens, etc.) are redacted.  Falls back to a
    generic ``comic-dl -u <url> -o <dir>`` when ``argv`` is unavailable.
    """
    if argv is None:
        argv = sys.argv
    parts: list[str] = []
    skip_next = False
    for i, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        low = token.lower()
        # Skip --debug-file and its value (not useful in a resume hint).
        if low == "--debug-file":
            if i + 1 < len(argv):
                skip_next = True
            continue
        # Skip global-only flags not meaningful for resume.
        if low in ("--no-color", "--no-banner", "--quiet", "-q"):
            continue
        if low.startswith("--color"):
            if "=" not in token and i + 1 < len(argv):
                skip_next = True
            continue
        # Redact values after flags that look secret-bearing.
        if _SECRET_PATTERNS.search(low) and i + 1 < len(argv):
            parts.append(token)
            parts.append("<redacted>")
            skip_next = True
            continue
        parts.append(token)
    if not parts:
        parts = ["comic-dl"]
    cmd = " ".join(parts)
    return cmd


def _cleanup_temp_dir() -> None:
    if _TMP_ROOT is None:
        return
    tmp_root = _TMP_ROOT
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)


async def _close_webview_session() -> None:
    """Best-effort teardown of any long-lived webview request session."""
    try:
        from .. import webview

        await webview.close_session()
    # Teardown must never mask a result.
    except Exception:  # nosec B110
        pass


def _open_library(output_dir: Path) -> Library | None:
    """Create the output directory and open its library, failing cleanly.

    Returns ``None`` (after printing a normal error) when the output
    directory cannot be created — a raw ``Path.mkdir`` traceback otherwise
    escapes ``Library.open``. A traceback is only shown at ``-vvv``.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        library = Library(library_path(output_dir))
        library.open()
        return library
    except OSError as exc:
        report_error(
            exc,
            context=f"Output directory not writable: {output_dir}",
            hint=exc.strerror or str(exc),
        )
        return None


def _redownload_estimate(
    urls: list[str], index: dict[str, Path]
) -> tuple[int, int]:
    """Count existing local chapters a ``--force`` run would re-fetch.

    Walks each already-downloaded series directory reachable from ``index``
    (normalized URL → one file inside the series directory) counting
    ``*.cbz``/``*.md`` files and summing their sizes. Used to size the
    ``--force`` batch confirmation warning.
    """
    chapters = 0
    total_bytes = 0
    seen: set[Path] = set()
    for url in urls:
        path = index.get(normalize_url_key(url))
        if path is None:
            continue
        base = path.parent if path.is_file() else path
        if base in seen:
            continue
        seen.add(base)
        for pattern in (*ARCHIVE_PATTERNS, "*.md"):
            for p in base.glob(pattern):
                chapters += 1
                with contextlib.suppress(OSError):
                    total_bytes += p.stat().st_size
    return chapters, total_bytes


def _url_origin(args: argparse.Namespace, url: str) -> str:
    """Suffix like `` (urls.txt:12)`` for a URL loaded from a list file."""
    origins = getattr(args, "url_origins", None)
    if not origins:
        return ""
    origin = origins.get(url)
    return f" ({origin})" if origin else ""


_SERIES_URL_CHECKERS = {
    "webtoons.com": is_webtoon_series_url,
    "flamecomics.xyz": is_flamecomics_series_url,
    "fsicomics.com": is_fsicomics_series_url,
    "gedecomix.com": is_gedecomix_series_url,
    "asurascans.com": is_asurascans_series_url,
    "kagane.to": is_kagane_series_url,
}


def _classify_preview_entry(
    entry: dict, url: str, index: dict[str, Path], force: bool
) -> dict:
    """Tag a preview entry with its download action, mirroring live logic.

    Chapter URLs classify against the local ``index`` (skip / redownload /
    download). Series URLs always resolve to ``download``: the series scrape
    lists chapters, and each chapter is individually skipped at download time,
    so the preview aggregates them as "would download".
    """
    if entry["kind"] == "series":
        entry["action"] = "download"
        return entry
    existing = index.get(normalize_url_key(url))
    if existing is not None and not force:
        entry["action"] = "skip"
        entry["existing"] = existing.name
    elif existing is not None:
        entry["action"] = "redownload"
    else:
        entry["action"] = "download"
    return entry


async def _preview_url(
    url: str, index: dict[str, Path], force: bool
) -> dict:
    """Resolve ``url`` into a dry-run preview entry without writing anything.

    Fetches only the metadata a real run would (chapter or series title and
    a page/chapter count), plus a best-effort byte estimate — the site-provided
    size when the source reports one, otherwise a ``content-length`` probe on a
    sample of pages (same ``probe_download_size`` the live run uses, capped at
    8 s). Then classifies the action against the local ``index``.
    Resolution/validation errors become an ``error`` entry that reports a
    friendly message instead of crashing the preview.
    """
    domain = _extract_domain(url)
    entry: dict = {
        "url": url,
        "domain": domain,
        "kind": "",
        "title": "",
        "detail": "",
        "action": "",
    }

    try:
        series_scraper = get_series_scraper(domain)
        series_check = _SERIES_URL_CHECKERS.get(domain)
        if series_scraper is not None and series_check is not None and series_check(url):
            async with AsyncSession(**_with_referer(url)) as client:
                info = await series_scraper.scrape_series(url, client)
            entry["kind"] = "series"
            entry["title"] = (info.series_title or "").strip()
            chapters = getattr(info, "chapters", None) or []
            entry["detail"] = f"{len(chapters)} chapter" + (
                "s" if len(chapters) != 1 else ""
            )
            entry["pages"] = len(chapters)
            return _classify_preview_entry(entry, url, index, force)
        scraper = get_chapter_scraper(domain)
        if scraper is None and generic_enabled():
            generic = get_generic_scraper()
            if generic is not None:
                if domain:
                    print_dim(f"Using generic extraction for {domain} {glyphs().ellipsis}")
                async with AsyncSession(**_with_referer(url)) as client:
                    try:
                        kind = await generic.detect(url, client)
                    except Exception:
                        kind = None
                if kind == "series":
                    async with AsyncSession(**_with_referer(url)) as client:
                        info = await generic.scrape_series(url, client)
                    entry["kind"] = "series"
                    entry["title"] = (info.series_title or "").strip()
                    chapters = getattr(info, "chapters", None) or []
                    entry["detail"] = f"{len(chapters)} chapter" + (
                        "s" if len(chapters) != 1 else ""
                    )
                    entry["pages"] = len(chapters)
                    return _classify_preview_entry(entry, url, index, force)
                if kind == "gallery":
                    scraper = generic
        if scraper is None:
            entry["action"] = "error"
            entry["detail"] = (
                f"Unsupported URL for domain {domain!r}." if domain
                else "Unsupported URL."
            )
            return entry
        async with AsyncSession(**_with_referer(url)) as client:
            meta = await scraper.scrape(url, client)
        entry["kind"] = "chapter"
        entry["title"] = (
            (meta.chapter_title or meta.series_title or "").strip()
        )
        pages = meta.total_pages or len(getattr(meta, "images", None) or [])
        entry["detail"] = f"{pages} page" + ("s" if pages != 1 else "")
        entry["pages"] = pages
        entry["series"] = (meta.series_title or "").strip()
        entry["post_id"] = meta.post_id or ""
        entry["estimated_size"] = meta.estimated_size or 0
        # Already-downloaded URLs never write bytes, so skip the probe for them.
        entry["size"] = 0
        existing = index.get(normalize_url_key(url))
        if pages > 0 and (force or existing is None):
            if entry["estimated_size"]:
                entry["size"] = entry["estimated_size"]
            else:
                try:
                    entry["size"] = await asyncio.wait_for(
                        probe_download_size(meta.images, url), timeout=5.0
                    )
                except Exception:
                    entry["size"] = 0
    except ValueError as exc:
        entry["kind"] = ""
        entry["title"] = ""
        entry["detail"] = ""
        entry["action"] = "error"
        entry["error"] = str(exc) or "The page could not be processed."
        return entry
    except Exception as exc:
        entry["kind"] = ""
        entry["title"] = ""
        entry["detail"] = ""
        entry["action"] = "error"
        entry["error"] = _classify(exc)[0]
        return entry

    return _classify_preview_entry(entry, url, index, force)


def _dry_run_dest(
    entry: dict, args: argparse.Namespace, index: dict[str, Path]
) -> Path | None:
    """Best-effort destination a live run would write for ``entry``.

    Reuses the live archive-path resolver so the preview shows the sanitized
    series directory, the disambiguated ``Title (post_id)`` stem, and the
    effective ``--format`` extension — without writing anything. Returns
    ``None`` when no destination applies (error entries, or a chapter that
    already exists and would be skipped).
    """
    if entry.get("error") or entry.get("action") in (None, "skip"):
        return None
    title = entry.get("title") or ""
    if not title or entry.get("kind") not in ("chapter", "series"):
        return None
    series_dir = Path(args.output) / sanitize_filename(entry.get("series") or title)
    fmt = getattr(args, "format", "cbz") or "cbz"
    dest = _resolve_archive_path(
        series_dir,
        title,
        entry["url"],
        entry.get("post_id", ""),
        bool(getattr(args, "force", False)),
        quiet=True,
        fmt=fmt,
    )
    if dest is not None:
        return dest
    # _resolve_archive_path returned None: an existing archive means skip.
    # Fall back to the plain target name so the preview still shows where the
    # file would live were it not already present.
    return series_dir / f"{sanitize_filename(title)}.{fmt}"


def _report_dry_run(
    entries: list[dict], urls: list[str], args: argparse.Namespace, index: dict[str, Path]
) -> int:
    """Render a resolved dry-run — ``--json`` to stdout, the human preview to
    stderr.

    The human preview is preflight/diagnostic output: routing it to stderr
    keeps stdout clean for scripts (``comic-dl --dry-run > out 2> err``) and
    matches the prelude lines (``Output directory:``, ``Loaded N URLs``) that
    already land on stderr. ``--json`` stays on stdout so pipes get exactly
    the payload.
    """
    if args.json:
        console.print(json.dumps(
            {"schema_version": JSON_SCHEMA_VERSION, "urls": entries}, indent=2,
        ), soft_wrap=True)
        return EXIT_OK

    out = _active_console()
    total = len(urls)
    compress = getattr(args, "compress", "stored") or "stored"
    compression_suffix = "" if compress == "stored" else f" [{compress}]"

    print_dim(f"Concurrency: {args.parallel} URLs in parallel "
              f"{glyphs().bullet} {args.concurrency} page workers")

    counts = {"download": 0, "skip": 0, "redownload": 0, "error": 0}
    for entry in entries:
        counts[entry["action"]] += 1

    for i, entry in enumerate(entries, start=1):
        idx = f"[{i:02d}/{total}] " if total > 1 else ""
        # Rich markup consumes ``[``-delimited spans; escape the dynamic parts
        # so URLs/titles/compression labels survive verbatim.
        head = f"  {idx}would {entry['action']:<9} {esc(entry['url'])}"
        if entry.get("error"):
            out.print(f"{head}  ({esc(entry['error'])})")
        elif entry["action"] == "skip":
            out.print(
                f"{head}  (already downloaded as {esc(entry['existing'])})"
            )
        elif entry["kind"]:
            dest = _dry_run_dest(entry, args, index)
            dest_suffix = ""
            if dest is not None:
                rel = (
                    dest.relative_to(Path(args.output))
                    if dest.is_relative_to(Path(args.output))
                    else dest
                )
                dest_suffix = f"  -> {esc(str(rel))}{esc(compression_suffix)}"
            title_repr = esc(repr(entry["title"]))
            out.print(
                f"{head}  ({entry['kind']} {title_repr} {glyphs().dot} "
                f"{esc(entry['detail'])}"
                + (f" {glyphs().dot} ~{format_bytes(entry['size'])}" if entry.get("size") else "")
                + f"){dest_suffix}"
            )
        else:
            out.print(head)

    out.print()
    out.print(
        "Dry run: "
        f"{counts['download']} would download, "
        f"{counts['skip']} already downloaded, "
        f"{counts['redownload']} would redownload, "
        f"{counts['error']} errors."
    )

    est_bytes = sum(
        e.get("size") or 0 for e in entries
        if e.get("action") in ("download", "redownload")
    )
    if est_bytes > 0:
        out.print(f"Estimated total: ~{format_bytes(est_bytes)}")
    _check_disk_space(Path(args.output), est_bytes)
    out.print("Nothing was written.")
    if args.force:
        chapters, size = _redownload_estimate(urls, index)
        if chapters:
            out.print(
                f"  ...of which {chapters} existing chapters "
                f"(~{format_bytes(size)}) would be re-fetched."
            )
    return EXIT_OK


async def _run_dry_run(
    urls: list[str], args: argparse.Namespace, index: dict[str, Path]
) -> int:
    """Preview a run without writing anything.

    Resolves each URL asynchronously — chapter vs series, the displayed
    title, and a cheap size signal (page or chapter count) — so the preview
    reflects what a real run would do, not just what the local index says.
    Nothing is written and no prompt is shown. With ``--json`` the same
    entries are emitted as structured output; otherwise a live counter
    tracks resolution progress (on a TTY) and a human-readable preview goes
    to stderr. At ``-v`` each resolved URL is logged with its page/byte count
    so a stalling gallery is identifiable even without the TTY overlay.
    """
    if args.json and console.is_terminal and not args.quiet:
        print_dim("Resolving URLs for dry-run...")

    sem = asyncio.Semaphore(min(8, len(urls) or 1))

    async def _probe_one(u: str) -> dict:
        async with sem:
            return await _preview_url(u, index, args.force)

    overlay = (
        bool(urls)
        and not args.json
        and not args.quiet
        and console.is_terminal
    )
    if overlay:
        async with Activity() as act:
            main = act.row("main")
            main.stage(f"Resolving URLs for dry-run{glyphs().ellipsis}")
            tasks = {url: asyncio.create_task(_probe_one(url)) for url in urls}
            # as_completed yields the same Task objects passed in, so a
            # reverse map resolves url in O(1) instead of scanning per
            # completion. (as_completed's Future type is the Task itself.)
            task_to_url = {t: u for u, t in tasks.items()}
            resolved: dict[str, dict] = {}
            for fut in asyncio.as_completed(tasks.values()):
                url = task_to_url[cast(asyncio.Task[dict], fut)]
                resolved[url] = await fut
                main.set_label(_short_url_label(url))
                main.stage(
                    f"Resolving URLs for dry-run{glyphs().ellipsis} "
                    f"{len(resolved)}/{len(urls)} {_short_url_label(url)}"
                )
            entries = [resolved[u] for u in urls]
    else:
        entries = await asyncio.gather(*(_probe_one(u) for u in urls))
        for u, entry in zip(urls, entries, strict=True):
            vlog(
                VERBOSE,
                f"resolved {_short_url_label(u)} "
                f"({entry.get('detail') or entry.get('action', '')}"
                + (f" {glyphs().dot} ~{format_bytes(entry['size'])}" if entry.get("size") else "")
                + ")",
            )

    return _report_dry_run(entries, urls, args, index)


def _short_url_label(url: str) -> str:
    """Tail of a URL's path (or host), truncated, for compact live status."""
    path = urlparse(url).path.rstrip("/")
    label = path.rsplit("/", 1)[-1] or urlparse(url).hostname or url
    return label if len(label) <= 48 else label[:45] + glyphs().ellipsis


def _maybe_first_run_hint(args: argparse.Namespace, completed: int) -> None:
    """Print one muted tip on the first successful batch.

    A missing config file means ``comic-dl config init`` has never been run,
    so the first batch is the natural place to point at it; the hint stops
    once the file exists. Never shown for scripted (piped/JSON/quiet) runs.
    """
    if (
        completed <= 0
        or args.quiet
        or getattr(args, "json", False)
        or not console.is_terminal
        or not sys.stdin.isatty()
        or config_path().exists()
    ):
        return
    print_dim(
        "Tip: 'comic-dl config init' saves your default output & options; "
        "'comic-dl update <series>' fetches new chapters later."
    )


async def _run_urls(urls: list[str], args: argparse.Namespace) -> int:
    total = len(urls)
    batch_started = time.monotonic()
    succeeded = 0
    skipped = 0
    failed_batch: list[str] = []

    interactive = (
        not args.json
        and not args.quiet
        and args.chapters is None
        and not getattr(args, "urls_from_file", False)
        and sys.stdin.isatty()
        and console.is_terminal
    )

    vlog(
        VERBOSE,
        f"Options: concurrency={args.concurrency}, max-size={format_option_size(args.max_size)}, "
        f"max-image-size={format_option_size(args.max_image_size)}, "
        f"chapters={args.chapters or 'all'}",
    )

    index: dict[str, Path] = {}
    if not args.force or args.dry_run or total > 1:
        index = await asyncio.to_thread(_build_downloaded_index, Path(args.output))
        vlog(
            VERBOSE,
            f"index: {len(index)} already-downloaded item(s) in {args.output}",
        )

    if args.dry_run:
        return await _run_dry_run(urls, args, index)

    if args.force and total > 1:
        chapters, size = _redownload_estimate(urls, index)
        if chapters > 0:
            if not interactive:
                print_error(
                    f"--force would redownload {chapters} existing chapters "
                    f"(~{format_bytes(size)})."
                )
                print_dim("Pass --dry-run to preview this redownload.")
                # Same class as an unanswerable confirmation prompt (EOF on
                # Prompt.ask, library remove --json without -y): the run is
                # refused pending a confirmation it cannot obtain, so exit
                # 130 rather than usage-error.
                return EXIT_INTERRUPTED
            console.print()
            print_header("Confirm redownload")
            print_meta(
                "Existing content",
                f"{chapters} chapters (~{format_bytes(size)})",
            )
            try:
                answer = Prompt.ask(
                    f"Redownload {chapters} existing chapters across "
                    f"{total} URLs?",
                    choices=["y", "n"],
                    default="n",
                    console=err_console,
                )
            except EOFError:
                console.print()
                return EXIT_INTERRUPTED
            if answer.strip().lower() != "y":
                print_dim("Cancelled.")
                return EXIT_OK

    library = _open_library(args.output)
    if library is None:
        return EXIT_ERROR

    try:
        batch_parallel = max(1, args.parallel)
        sem = asyncio.Semaphore(batch_parallel)
        # Results keyed by input index so JSON output stays in URL order
        # regardless of which URL finishes first.
        ordered_results: dict[int, dict] = {}
        batch_quit = False
        failed_details: list[tuple[str, str]] = []
        # One shared Live for the whole batch: each URL gets its own labeled
        # row, so URLs run in parallel without the UI gate serializing them.
        batch_act = (
            Activity(quiet=False)
            if not args.quiet and not args.json and total > 1
            else None
        )

        def _label_for(url: str) -> str:
            return _short_url_label(url) if batch_parallel > 1 else ""

        if batch_act is not None:
            # Pre-create one queued row per URL so the Overall header can show
            # done/running/queued from the start instead of growing incrementally.
            # The displayed label carries the same [k/total] index the dry-run
            # preview uses, so failures correlate 1:1 between the two views.
            batch_act.begin_batch(total)
            for i, u in enumerate(urls):
                label = _label_for(u)
                tag = f"[{i + 1}/{total}] " if batch_parallel > 1 else ""
                batch_act.add_queued_row(
                    label or f"url-{i}", label=f"{tag}{label or u}"
                )

        async def _process_one(url: str, idx: int) -> None:
            nonlocal succeeded, skipped, failed_batch, failed_details, batch_quit
            async with sem:
                if stop_requested():
                    return
                label = _label_for(url)
                row_key = label if label else f"url-{idx}"
                existing = None if args.force else index.get(normalize_url_key(url))
                if existing is not None:
                    skipped += 1
                    trace(f"skip: {url} — already downloaded at {existing.name}")
                    if batch_act is not None:
                        batch_act.finish_row(row_key, ok=True, message="already downloaded")
                    if not args.quiet:
                        print_skipped(
                            f"{existing.name} already exists. Skipping."
                            f"{_url_origin(args, url)}"
                        )
                    ordered_results[idx] = _url_result(
                        url,
                        DownloadStats(
                            status="skipped",
                            output_path=str(existing),
                        ),
                        0.0,
                    )
                    return
                stats = DownloadStats()
                started = time.monotonic()
                if batch_act is not None:
                    batch_act.mark_running(row_key, stage="Starting...")
                try:
                    status, _ = await process_url(
                        url=url,
                        output_dir=args.output,
                        concurrency=args.concurrency,
                        force=args.force,
                        max_image_size=args.max_image_size,
                        max_total_size=args.max_size,
                        quiet=args.quiet,
                        chapters=args.chapters,
                        interactive=interactive and batch_parallel == 1,
                        library=library if library.available else None,
                        stats=stats,
                        label=label,
                        activity=batch_act,
                        row_key=row_key,
                        chapter_parallel=args.chapter_parallel,
                        compression=getattr(args, "compress", "stored"),
                        fmt=getattr(args, "format", "cbz"),
                        failure_sink=failed_details,
                    )
                    duration_s = time.monotonic() - started
                    if status == "downloaded":
                        succeeded += 1
                        stats.status = "success"
                    elif status == "interrupted":
                        # Graceful stop: in-flight item finished, no new
                        # items will start.  Do NOT record as a failure —
                        # the caller handles the exit code.
                        return
                    elif status == "skipped":
                        skipped += 1
                        stats.status = "skipped"
                        print_skipped(f"Skipped: {url}{_url_origin(args, url)}")
                    elif status == "partial":
                        failed_batch.append(url)
                        stats.status = "partial"
                        if not args.quiet:
                            print_partial_block(
                                url,
                                missing=stats.missing_pages or 0,
                                total=stats.total_pages or 0,
                                output_dir=args.output,
                            )
                    else:
                        failed_batch.append(url)
                        stats.status = "failed"
                        # process_url reports most failures through the sink;
                        # only add a fallback detail when it did not.
                        if not any(u == url for u, _ in failed_details):
                            failed_details.append(
                                (
                                    f"Failed: {url}{_url_origin(args, url)}",
                                    stats.message or "Download failed.",
                                )
                            )
                    if batch_act is not None:
                        batch_act.finish_row(
                            row_key,
                            ok=status not in ("failed", "partial"),
                            message=stats.status,
                        )
                    ordered_results[idx] = _url_result(url, stats, duration_s)
                except asyncio.CancelledError:
                    raise
                except ChapterSelectionQuit:
                    # User cancelled from the chapter selector: exit 0.
                    batch_quit = True
                    return
                except Exception as exc:
                    duration_s = time.monotonic() - started
                    message, code = _classify(exc)
                    if VERBOSITY >= TRACE:
                        import traceback

                        traceback.print_exception(exc)
                    failed_details.append(
                        (f"Failed: {url}{_url_origin(args, url)}", message)
                    )
                    if batch_act is not None:
                        batch_act.finish_row(
                            row_key, ok=False, message=message
                        )
                    # A status branch (partial/failed) may already have
                    # recorded the URL before this exception escaped; do not
                    # paste the same URL into the tally twice.
                    if url not in failed_batch:
                        failed_batch.append(url)
                    # idx was assigned by enumerate(urls) at gather time, so
                    # it always equals urls.index(url) — O(1) instead of a
                    # rescan of the whole batch per completion.
                    ordered_results[idx] = _url_result(
                        url,
                        DownloadStats(
                            status="failed",
                            error=code,
                            message=message,
                        ),
                        duration_s,
                    )

        if batch_act is not None:
            # Print a single batch header before the shared Live opens, so the
            # per-URL rows stay cleanly attributed below it.
            console.print()
            print_header(
                f"Processing {total} URLs ({batch_parallel} in parallel)"
            )
            console.print()
            async with batch_act:
                await asyncio.gather(*(_process_one(u, i) for i, u in enumerate(urls)))
        else:
            await asyncio.gather(*(_process_one(u, i) for i, u in enumerate(urls)))
        if batch_quit:
            return EXIT_OK
        if stop_requested():
            # Graceful stop: all in-flight items finished, no new ones
            # started.  Print the interrupt line and exit 130.
            progress = active_snapshot()
            partial = bool(active_partial_files())
            resume_cmd = _RESUME_CMD or ""
            print_interrupt(
                progress if partial else "",
                partial=partial,
                resume_cmd=resume_cmd,
            )
            return EXIT_INTERRUPTED
        if total == 1 and failed_details:
            # Single-URL batches skip the summary block below, so render the
            # deferred failure recap here instead of dropping it.
            print_failure_recap(failed_details)
        elif total == 1 and not args.quiet:
            # Symmetry: partial and failed runs end with their own verdict
            # block, so a clean download must too — "Saved:" alone scrolls
            # past mid-live and reads as "did it actually finish?".
            done = ordered_results.get(0) or {}
            if done.get("status") == "success":
                out = done.get("output_path") or ""
                name = Path(out).name if out else "archive"
                where = f" {glyphs().dash} {args.output}" if args.output else ""
                print_success(f"Downloaded: {name}{where}")
        json_results = [ordered_results[i] for i in range(len(urls)) if i in ordered_results]
    finally:
        library.close()

    if args.json:
        if args.url is not None and total == 1 and json_results:
            payload: dict[str, object] = dict(json_results[0])
            payload["schema_version"] = JSON_SCHEMA_VERSION
        else:
            payload = {
                "schema_version": JSON_SCHEMA_VERSION,
                "urls": json_results,
                "succeeded": succeeded,
                "skipped": skipped,
                "failed": len(failed_batch),
            }
        console.print(json.dumps(payload, indent=2), soft_wrap=True)
        return EXIT_ERROR if failed_batch else EXIT_OK

    if total > 1:
        chapters = sum(
            r.get("chapters_downloaded") or 0 for r in ordered_results.values()
        )
        batch_bytes = sum(r.get("bytes") or 0 for r in ordered_results.values())
        elapsed_secs = time.monotonic() - batch_started
        # Batch runs carry a grand-total footer; the heavy Rule divider is for
        # interactive multi-URL runs, not file batches.
        if not getattr(args, "urls_from_file", False):
            console.print(Rule(style="dim"))
        print_batch_summary(
            succeeded, skipped, len(failed_batch), failed_batch,
            chapters=chapters,
            total_bytes=batch_bytes,
            elapsed_secs=elapsed_secs,
            failure_details=failed_details,
        )
        console.print()

    if failed_batch:
        return EXIT_ERROR
    _maybe_first_run_hint(args, succeeded + skipped)
    return EXIT_OK


async def _run_update(argv: list[str]) -> int:
    """Re-scrape tracked series and download only newly-released chapters.

    ``comic-dl update <series|all>`` re-fetches each tracked series page,
    diffs its current chapter list against the library DB, and downloads just
    the chapters that are not already recorded. ``--parallel`` updates up to
    N series at once (default 1 keeps runs sequential); the per-host rate
    limiter still paces every request, so multiple series cannot violate the
    politeness budget. Series without a stored source URL or a series scrape
    endpoint are skipped with a notice. Uses :func:`_process_series`, so
    ``last_checked`` / ``last_updated`` are refreshed for free.
    """
    parser = ComicArgumentParser(
        prog="comic-dl update", description="Download new chapters for tracked series.",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Library root directory (default: per-user downloads folder)",
    )
    parser.add_argument(
        "-c", "--concurrency", type=int, default=5,
        help="Max parallel chapter downloads (default: 5)",
    )
    parser.add_argument(
        "--chapter-parallel",
        type=int,
        default=1,
        help="Max chapters of a series downloading at once (1-8; default 1)",
    )
    parser.add_argument(
        "-p", "--parallel",
        type=int,
        default=1,
        help="Max series updating at once (1-16; default 1)",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress progress output",
    )
    parser.add_argument(
        "--compress",
        nargs="?",
        const="deflate",
        default=None,
        metavar="MODE",
        help=(
            "CBZ compression: stored (default) | deflate | deflate:0-9. "
            "Overrides [archive] compression"
        ),
    )
    parser.add_argument(
        "--format",
        choices=("cbz", "zip", "cbt"),
        default=None,
        metavar="FORMAT",
        help=(
            "Archive format: cbz (default) | zip | cbt. Overrides "
            "[archive] format"
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON on stdout",
    )
    parser.add_argument(
        "target",
        help="Series title, series ID, or series URL — or 'all' for every tracked series",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    if args.output is None:
        args.output = configured_output_dir()
    if args.concurrency < 1:
        print_error("--concurrency must be at least 1.")
        return EXIT_USAGE
    if args.parallel < 1:
        print_error("--parallel must be at least 1.")
        return EXIT_USAGE
    if args.parallel > MAX_PARALLEL:
        print_warning(f"--parallel capped to {MAX_PARALLEL}.")
        args.parallel = MAX_PARALLEL
    if getattr(args, "compress", None) is None:
        archive_cfg = load_config().get("archive")
        args.compress = (
            archive_cfg.get("compression")
            if isinstance(archive_cfg, dict) and isinstance(archive_cfg.get("compression"), str)
            else "stored"
        )
    fmt: str | None = getattr(args, "format", None)
    if fmt is None:
        archive_cfg = load_config().get("archive")
        fmt = (
            archive_cfg.get("format")
            if isinstance(archive_cfg, dict) and isinstance(archive_cfg.get("format"), str)
            else "cbz"
        )
    try:
        args.format = _normalize_format(fmt or "cbz")
    except ValueError as exc:
        print_error(f"{exc}")
        return EXIT_USAGE

    library = _open_library(args.output)
    if library is None:
        return EXIT_ERROR
    try:
        if not library.available:
            print_error(f"No library database at {library_path(args.output)}.")
            print_dim("Download a series first, or point -o at the right output root.")
            return EXIT_ERROR

        if args.target.strip().lower() == "all":
            rows = library.list_series()
            series = [
                (s["series_id"], s["title"], s.get("source") or "")
                for s in rows
            ]
            if not series:
                print_dim("Library is empty — nothing to update.")
                return EXIT_OK
        else:
            match = _resolve_series(library, args.target)
            if match is None:
                return EXIT_USAGE
            series = [(match["series_id"], match["title"], match.get("source") or "")]

        checked = 0
        changed = 0
        skipped = 0
        failed: list[str] = []
        results: list[dict | None] = [None] * len(series)
        series_parallel = max(1, args.parallel)
        sem = asyncio.Semaphore(series_parallel)
        # One shared Live for the whole update so parallel series each get
        # their own labeled row instead of fighting over the terminal; a
        # single series (or -p 1) keeps the current per-series output as-is.
        batch_act = (
            Activity(quiet=False)
            if not args.quiet and not args.json and len(series) > 1 and series_parallel > 1
            else None
        )
        if batch_act is not None:
            batch_act.begin_batch(len(series))
            for i, (_sid, title, _source) in enumerate(series):
                batch_act.add_queued_row(f"series-{i}", label=title)

        def _series_key(idx: int) -> str:
            return f"series-{idx}"

        async def _update_one(
            series_id: str, title: str, source: str, idx: int,
        ) -> None:
            nonlocal checked, changed, skipped, failed
            async with sem:
                if stop_requested():
                    return
                row_key = _series_key(idx)
                if not source:
                    print_warning(f"No source URL recorded for '{title}'; skipping.")
                    skipped += 1
                    results[idx] = {
                        "series_id": series_id, "title": title, "status": "skipped",
                    }
                    if batch_act is not None:
                        batch_act.finish_row(row_key, ok=False, message="no source URL")
                    return
                source = normalize_url(source)
                domain = _extract_domain(source)
                scraper = get_series_scraper(domain)
                if scraper is None and generic_enabled():
                    generic = get_generic_scraper()
                    if generic is not None:
                        if domain:
                            print_dim(f"Using generic extraction for {domain} {glyphs().ellipsis}")
                        async with AsyncSession(**_with_referer(source)) as client:
                            try:
                                kind = await generic.detect(source, client)
                            except Exception:
                                kind = None
                        if kind == "series":
                            scraper = generic
                if scraper is None:
                    print_warning(
                        f"No series page endpoint for '{title}' ({domain}); skipping."
                    )
                    skipped += 1
                    results[idx] = {
                        "series_id": series_id, "title": title, "status": "skipped",
                    }
                    if batch_act is not None:
                        batch_act.finish_row(row_key, ok=False, message="no series endpoint")
                    return
                if batch_act is not None:
                    batch_act.mark_running(row_key, stage="Checking...")
                try:
                    before = len(library.get_chapters(series_id))
                    ok = await _process_series(
                        scraper=scraper,
                        url=source,
                        output_dir=args.output,
                        concurrency=args.concurrency,
                        force=False,
                        quiet=args.quiet,
                        chapter_parallel=args.chapter_parallel,
                        compression=getattr(args, "compress", "stored"),
                        fmt=getattr(args, "format", "cbz"),
                        library=library,
                        activity=batch_act,
                        row_key=row_key,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed.append(title)
                    results[idx] = {
                        "series_id": series_id, "title": title, "status": "failed",
                    }
                    if batch_act is not None:
                        batch_act.finish_row(row_key, ok=False, message="failed")
                    if not args.quiet:
                        report_error(
                            exc,
                            context=f"Update failed for '{title}'",
                            hint="Run again with -vvv for a traceback.",
                        )
                    return
                checked += 1
                had_new = ok and len(library.get_chapters(series_id)) > before
                if had_new:
                    changed += 1
                results[idx] = {
                    "series_id": series_id,
                    "title": title,
                    "status": "changed" if had_new else "unchanged",
                }
                if batch_act is not None:
                    batch_act.finish_row(
                        row_key,
                        ok=ok,
                        message="new chapters" if had_new else "unchanged",
                    )

        if batch_act is not None:
            console.print()
            print_header(f"Updating {len(series)} series ({series_parallel} in parallel)")
            console.print()
            async with batch_act:
                await asyncio.gather(
                    *(
                        _update_one(sid, title, source, i)
                        for i, (sid, title, source) in enumerate(series)
                    )
                )
        else:
            await asyncio.gather(
                *(
                    _update_one(sid, title, source, i)
                    for i, (sid, title, source) in enumerate(series)
                )
            )
        results = [r for r in results if r is not None]

        if stop_requested():
            # Graceful stop: all in-flight series finished, no new ones
            # started.  Print the interrupt line and exit 130.
            progress = active_snapshot()
            partial = bool(active_partial_files())
            resume_cmd = _RESUME_CMD or ""
            print_interrupt(
                progress if partial else "",
                partial=partial,
                resume_cmd=resume_cmd,
            )
            return EXIT_INTERRUPTED

        if args.json:
            console.print(json.dumps({
                "schema_version": JSON_SCHEMA_VERSION,
                "checked": checked,
                "changed": changed,
                "skipped": skipped,
                "failed": failed,
                "series": results,
            }, indent=2), soft_wrap=True)
            return EXIT_ERROR if failed else EXIT_OK

        console.print(Rule(style="dim"))
        if args.quiet:
            return EXIT_ERROR if failed else EXIT_OK
        if checked:
            print_success(
                f"Checked {checked} series, {changed} had new chapters."
            )
        if skipped:
            print_dim(f"Skipped {skipped} series (no source or no series endpoint).")
        if failed:
            print_error(f"Failed to update: {', '.join(failed)}")
        print_dim(f"Library: {library_path(args.output)}")
        return EXIT_ERROR if failed else EXIT_OK
    finally:
        library.close()


def _invalid_cookie_host(host: str) -> str | None:
    """Return a reason string when ``host`` can't be a cookie domain, else ``None``.

    Rejects anything that is not a bare host: no scheme, port, path, query,
    whitespace, or userinfo. Allows a leading ``.`` for domain cookies and
    typical IDN/punycode hostnames.
    """
    if not host or not host.strip():
        return "empty host"
    candidate = host.strip().lstrip(".")
    if not candidate or any(ch.isspace() for ch in host):
        return "host must not contain whitespace"
    if "://" in host or "/" in host or "@" in host or "?" in host or "#" in host:
        return "host must be a bare hostname (no scheme, port, path, or query)"
    if ":" in host:
        return "host must not include a port"
    try:
        parsed = urlparse(f"https://{candidate}/")
    except ValueError:
        return "unparseable host"
    if parsed.hostname != candidate.lower():
        return "not a valid hostname"
    return None


def _run_cookie(argv: list[str]) -> int:
    """Manage the persistent cookie jar: ``cookie ls`` / ``cookie set`` / ``cookie clear``."""
    parser = ComicArgumentParser(
        prog="comic-dl cookie",
        description="Inspect or clear the persistent cookie jar.",
    )
    sub = parser.add_subparsers(
        dest="action", required=True, parser_class=ComicArgumentParser,
    )
    ls = sub.add_parser(
        "ls", help="list stored cookies (optionally for one host)",
    )
    ls.add_argument("host", nargs="?", default=None)
    ls.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON on stdout",
    )
    st = sub.add_parser(
        "set", help="store a cookie for a host",
    )
    st.add_argument("host", help="host (e.g. kagane.to)")
    st.add_argument("name", help="cookie name (e.g. cf_clearance)")
    st.add_argument("value", help="cookie value")
    st.add_argument(
        "--expires",
        type=int,
        default=None,
        help="expiry as a Unix epoch timestamp (default: session/never)",
    )
    cl = sub.add_parser(
        "clear", help="clear cookies (optionally for one host)",
    )
    cl.add_argument("host", nargs="?", default=None)
    cl.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    from ..cookies import CookieJar

    jar = CookieJar()

    if args.action == "ls":
        rows = jar.list(args.host)
        if args.json:
            console.print(json.dumps(
                {"schema_version": JSON_SCHEMA_VERSION, "cookies": rows},
                indent=2,
            ), soft_wrap=True)
            return EXIT_OK
        if not rows:
            if args.host:
                print_dim(f"No cookies stored for host '{args.host}'.")
            else:
                print_dim("No cookies stored.")
            return EXIT_OK
        if args.host:
            print_success(f"{len(rows)} cookie(s) for '{args.host}':")
        else:
            print_success(f"{len(rows)} cookie(s) stored:")
        for r in rows:
            expiry = "session" if r["expires"] is None else (
                datetime.fromtimestamp(r["expires"], UTC).strftime("%Y-%m-%d %H:%M UTC")
            )
            print_dim(f"  {r['host']}  {r['name']}  (path={r['path']}, expires={expiry})")
        return EXIT_OK

    if args.action == "set":
        bad_host = _invalid_cookie_host(args.host)
        if bad_host:
            print_error(f"Invalid cookie host {args.host!r}: {bad_host}")
            return EXIT_USAGE
        jar.set(args.host, args.name, args.value, expires=args.expires)
        if args.expires is None:
            print_success(
                f"Stored cookie '{args.name}' for '{args.host}' (session/never)."
            )
        else:
            when = datetime.fromtimestamp(args.expires, UTC).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            print_success(
                f"Stored cookie '{args.name}' for '{args.host}' (expires {when})."
            )
        return EXIT_OK

    # clear
    target = f" for '{args.host}'" if args.host else ""
    if not args.yes:
        if _is_interactive_output():
            confirmed = Confirm.ask(f"Clear all cookies{target}?")
            if not confirmed:
                print_dim("Aborted.")
                return EXIT_OK
        else:
            # Non-interactive (pipes/CI) without -y: refuse rather than
            # silently clearing the jar — mirrors the `remove` command.
            console.print()
            print_error("Clearing cookies requires confirmation.")
            print_dim("Re-run with -y to clear without a prompt.")
            return EXIT_INTERRUPTED
    jar.clear(args.host)
    print_success(f"Cleared cookies{target}.")
    return EXIT_OK


def _run_cache(argv: list[str]) -> int:
    """Manage the scrape response cache: ``cache clear`` / ``cache status``."""
    parser = ComicArgumentParser(
        prog="comic-dl cache",
        description="Inspect or clear the on-disk scrape response cache.",
    )
    sub = parser.add_subparsers(
        dest="action", required=True, parser_class=ComicArgumentParser,
    )
    cl = sub.add_parser(
        "clear", help="delete every cached scrape response",
    )
    cl.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    sub.add_parser(
        "status", help="show the cache location, TTL, and entry count",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    from ..cache import cache_dir_path, cache_max_bytes, cache_ttl_hours
    from ..cache import clear as cache_clear

    if args.action == "status":
        root = cache_dir_path()
        count = 0
        total = 0
        if root.is_dir():
            for p in root.iterdir():
                if p.is_file() and p.suffix == ".dat":
                    count += 1
                    with contextlib.suppress(OSError):
                        total += p.stat().st_size
        print_success(f"Cache directory: {root}")
        print_success(f"Cache TTL: {cache_ttl_hours()}h")
        print_success(f"Size budget (GC trigger): {format_bytes(cache_max_bytes())}")
        print_success(f"Stored: {count} entr{'y' if count == 1 else 'ies'}, {format_bytes(total)}")
        return EXIT_OK

    if not args.yes:
        if _is_interactive_output():
            confirmed = Confirm.ask("Clear the entire scrape response cache?")
            if not confirmed:
                print_dim("Aborted.")
                return EXIT_OK
        else:
            console.print()
            print_error("Clearing the cache requires confirmation.")
            print_dim("Re-run with -y to clear without a prompt.")
            return EXIT_INTERRUPTED
    removed = cache_clear()
    print_success(f"Cleared {removed} cached response(s).")
    return EXIT_OK


def _install_signal_handlers() -> None:
    """Install interrupt handlers, tolerating platforms where signals differ.

    On Windows ``SIGTERM`` may be undefined or ``signal.signal`` may reject it
    depending on the Python build/event loop, so each registration is guarded.
    """
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        with contextlib.suppress(ValueError, OSError, RuntimeError):
            signal.signal(sig, _handle_interrupt)


def _unknown_command(command: str) -> int:
    err_console.print(f"  [bold {ERROR}]{glyphs().err}[/] error: unknown command '{command}'.")
    hint = suggest(
        command,
        sorted(
            set(_LIBRARY_COMMANDS)
            | {"update", "list-sources", "cookie", "cache", "config", "completion", "help"}
        ),
    )
    if hint and hint != command:
        err_console.print(f"  [{MUTED}]Did you mean:[/] {hint}?")
    return EXIT_USAGE


def _run_config(argv: list[str]) -> int:
    """Locate, inspect, or manage the config file.

    ``config`` (bare) and ``config show`` print the resolved effective
    configuration (documented defaults merged with the file). ``path`` prints
    the file path, ``list`` prints effective values as TOML, ``validate``
    type-checks the file, ``init`` writes a documented default, ``edit`` opens
    the file in ``$VISUAL``/``$EDITOR``.
    """
    parser = ComicArgumentParser(
        prog="comic-dl config",
        description="Locate, inspect, or manage the config.toml file.",
    )
    sub = parser.add_subparsers(
        dest="action", parser_class=ComicArgumentParser,
    )
    sub.add_parser("path", help="print the effective config file path")
    sub.add_parser(
        "show", help="print the resolved effective configuration (defaults + file)",
    )
    sub.add_parser("list", help="print effective values as TOML to stdout")
    sub.add_parser("validate", help="parse the config and report problems")
    init = sub.add_parser(
        "init", help="write a documented default config file (refuses to overwrite)",
    )
    init.add_argument(
        "--force", action="store_true", help="overwrite an existing config file",
    )
    sub.add_parser(
        "edit", help="open the config file in $VISUAL/$EDITOR (creates if missing)",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    path = config_path()
    action = args.action or "show"
    if action == "path":
        console.print(str(path))
        return EXIT_OK
    if action == "show":
        _print_effective_config(path)
        return EXIT_OK
    if action == "list":
        console.print(_toml_dump(effective_config()), end="", markup=False)
        return EXIT_OK
    if action == "validate":
        return _validate_config(path)
    if action == "init":
        if path.exists() and not args.force:
            print_error(f"Config file already exists: {path}")
            print_dim("Use --force to overwrite it.")
            return EXIT_USAGE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        except OSError as exc:
            print_error(f"Could not write {path}: {exc}")
            return EXIT_ERROR
        print_success(f"Wrote config to {path}")
        return EXIT_OK
    if action == "edit":
        return _edit_config(path)
    return EXIT_USAGE


def _print_effective_config(path: Path) -> None:
    """Print the resolved effective configuration plus a line naming the file."""
    if path.exists():
        print_dim(f"Loaded from: {path}")
    else:
        print_dim(f"No config file at {path}; built-in defaults apply.")
    console.print(_toml_dump(effective_config()), end="", markup=False)


def _toml_key(key: str) -> str:
    """Quote a TOML key when it is not a bare identifier (e.g. hosts with dots)."""
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json.dumps(key)


def _toml_scalar(value: Any) -> str:
    """Serialize a scalar to its TOML literal form."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    return str(value)


def _toml_dump(data: dict[str, Any]) -> str:
    """Serialize an effective-config dict to TOML.

    Handles the shape produced by :func:`effective_config`: flat scalars,
    ``[http]`` (with its inline ``rate`` map), ``[archive]``, and the
    ``[sources."<host>"]`` tables whose host keys need quoting.
    """
    lines: list[str] = []
    for key, value in data.items():
        if key == "sources" and isinstance(value, dict):
            for host, table in value.items():
                lines.append(f"[sources.{_toml_key(str(host))}]")
                if isinstance(table, dict):
                    for k, v in table.items():
                        lines.append(f"{_toml_key(k)} = {_toml_scalar(v)}")
            continue
        if isinstance(value, dict):
            lines.append(f"[{_toml_key(key)}]")
            for k, v in value.items():
                if isinstance(v, dict):
                    inner = ", ".join(
                        f"{_toml_key(str(kk))} = {_toml_scalar(vv)}"
                        for kk, vv in v.items()
                    )
                    lines.append(f"{_toml_key(k)} = {{ {inner} }}")
                else:
                    lines.append(f"{_toml_key(k)} = {_toml_scalar(v)}")
        else:
            lines.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    return "\n".join(lines) + "\n"


def _validate_config(path: Path) -> int:
    """Parse ``path`` and type-check the known keys; exit 0 when valid."""
    if not path.exists():
        print_dim(f"No config file at {path}; built-in defaults apply.")
        return EXIT_OK
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        print_error(f"Invalid TOML in {path}: {exc}")
        return EXIT_ERROR
    except OSError as exc:
        print_error(f"Could not read {path}: {exc}")
        return EXIT_ERROR
    if not isinstance(data, dict):
        print_error(f"{path} does not contain a TOML table.")
        return EXIT_ERROR

    problems: list[str] = []
    for key in ("concurrency", "parallel", "chapter_parallel"):
        if key in data:
            v = data[key]
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                problems.append(
                    f"{key}: expected an integer >= 1, got {v!r}"
                )
    for key in ("max_image_size", "max_size"):
        if key in data:
            v = data[key]
            if isinstance(v, bool) or not isinstance(v, (int, str)):
                problems.append(
                    f"{key}: expected a size like '100MB' or bytes, got {v!r}"
                )
            elif isinstance(v, str):
                try:
                    _parse_size(v)
                except (ValueError, argparse.ArgumentTypeError):
                    problems.append(
                        f"{key}: unparseable size {v!r}"
                    )

    http = data.get("http")
    if isinstance(http, dict):
        if "impersonate" in http and not isinstance(http["impersonate"], str):
            problems.append("http.impersonate: expected a string")
        if "solver" in http and http["solver"] not in {
            "auto", "impersonation", "webview", "off",
        }:
            problems.append(
                f"http.solver: expected auto|impersonation|webview|off, got "
                f"{http['solver']!r}"
            )
        for key in ("cookie-jar", "cache", "rate-enabled"):
            if key in http and not isinstance(http[key], bool):
                problems.append(f"http.{key}: expected true or false")
        if "cache-ttl" in http and (
            isinstance(http["cache-ttl"], bool)
            or not isinstance(http["cache-ttl"], int)
            or http["cache-ttl"] < 1
        ):
            problems.append(
                f"http.cache-ttl: expected an integer >= 1, got {http['cache-ttl']!r}"
            )
        if "cache-max-entries" in http and (
            isinstance(http["cache-max-entries"], bool)
            or not isinstance(http["cache-max-entries"], int)
            or http["cache-max-entries"] < 1
        ):
            problems.append(
                "http.cache-max-entries: expected an integer >= 1, "
                f"got {http['cache-max-entries']!r}"
            )
        rate = http.get("rate")
        if isinstance(rate, dict):
            for host, value in rate.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                    problems.append(
                        f"http.rate[{host!r}]: expected a positive number, got {value!r}"
                    )
        elif rate is not None:
            problems.append('http.rate: expected a table like { "host" = 1.5 }')

    archive = data.get("archive")
    if isinstance(archive, dict):
        if "format" in archive and archive["format"] not in {"cbz", "zip", "cbt"}:
            problems.append(
                f"archive.format: expected cbz|zip|cbt, got {archive['format']!r}"
            )
        if "compression" in archive:
            try:
                parse_compression(archive["compression"])
            except ValueError as exc:
                problems.append(f"archive.compression: {exc}")

    sources = data.get("sources")
    if isinstance(sources, dict):
        for host, table in sources.items():
            if not isinstance(table, dict):
                problems.append(f"sources[{host!r}]: expected a table")
                continue
            rate = table.get("rate")
            if rate is not None and (
                isinstance(rate, bool)
                or not isinstance(rate, (int, float))
                or rate <= 0
            ):
                problems.append(
                    f"sources[{host!r}].rate: expected a positive number, got {rate!r}"
                )

    if problems:
        for problem in problems:
            print_error(f"config: {problem}")
        print_dim(f"{len(problems)} problem(s) in {path}.")
        return EXIT_ERROR
    print_success(f"Config OK: {path}")
    return EXIT_OK


def _edit_config(path: Path) -> int:
    """Open ``path`` in ``$VISUAL``/``$EDITOR``, creating it first if needed."""
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        except OSError as exc:
            print_error(f"Could not create {path}: {exc}")
            return EXIT_ERROR
        print_dim(f"Created {path}; opening in your editor...")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        editor = _default_editor()
    try:
        # Argv is a list, never a shell string.
        return subprocess.call(  # nosec B603
            [editor, str(path)], shell=False
        )
    except OSError as exc:
        print_error(f"Could not start editor {editor!r}: {exc}")
        return EXIT_ERROR


def _completion_global_flags() -> list[str]:
    """Option strings of the first-stage parser, for completion candidates."""
    parser = _build_first_stage_parser()
    flags: list[str] = []
    for action in parser._actions:
        flags.extend(action.option_strings)
    return sorted(set(flags))


def _completion_commands() -> list[str]:
    """Top-level command names, for completion candidates."""
    return sorted(
        set(_LIBRARY_COMMANDS)
        | {"update", "list-sources", "cookie", "cache", "config", "completion", "help"}
    )


def _completion_script(shell: str) -> str:
    """Static completion script for ``shell`` (bash/zsh/fish), derived from
    the argparse definitions."""
    flags = " ".join(_completion_global_flags())
    commands = " ".join(_completion_commands())
    update_flags = (
        "-o --output -c --concurrency --chapter-parallel -q --quiet "
        "--compress --format --json"
    )
    lib_flags = "--json --dry-run -o --output"

    if shell == "bash":
        return f"""# bash completion for comic-dl
# Add to your shell:  source <(comic-dl completion bash)
_comic_dl_complete() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"
    if [[ "${{COMP_CWORD}}" -eq 1 ]]; then
        COMPREPLY=($(compgen -W "{commands} {flags}" -- "${{cur}}"))
        return
    fi
    case "${{COMP_WORDS[1]}}" in
        update) COMPREPLY=($(compgen -W "{update_flags}" -- "${{cur}}")); return ;;
        cookie) COMPREPLY=($(compgen -W "ls set clear" -- "${{cur}}")); return ;;
        cache)  COMPREPLY=($(compgen -W "clear status" -- "${{cur}}")); return ;;
        config) COMPREPLY=($(compgen -W "path show init --force" -- "${{cur}}")); return ;;
        list-sources) COMPREPLY=($(compgen -W "--json --plugin" -- "${{cur}}")); return ;;
        help)   COMPREPLY=($(compgen -W "{commands}" -- "${{cur}}")); return ;;
    esac
    COMPREPLY=($(compgen -W "{lib_flags} {flags}" -- "${{cur}}"))
}}
complete -o default -F _comic_dl_complete comic-dl
"""
    if shell == "zsh":
        return f"""#compdef comic-dl
# Add to your shell:  eval "$(comic-dl completion zsh)"
_comic_dl() {{
    local -a flags
    flags=({flags})
    if (( CURRENT == 2 )); then
        compadd -- {commands} ${{flags[@]}}
        return
    fi
    case "${{words[2]}}" in
        update) compadd -- {update_flags} ;;
        cookie) compadd -- ls set clear --json --expires -y --yes ;;
        cache)  compadd -- clear status ;;
        config) compadd -- path show init --force ;;
        list-sources) compadd -- --json --plugin ;;
        help)   compadd -- {commands} ;;
        *)      compadd -- ${{flags[@]}} ;;
    esac
}}
compdef _comic_dl comic-dl
"""
    if shell == "fish":
        return f"""# fish completion for comic-dl
# Add to your shell:  comic-dl completion fish | source
complete -c comic-dl -f
complete -c comic-dl -n "__fish_use_subcommand" -a "{commands}"
complete -c comic-dl -n "__fish_use_subcommand" -a "{flags}"
complete -c comic-dl -n "__fish_seen_subcommand_from update" -a "{update_flags}"
complete -c comic-dl -n "__fish_seen_subcommand_from cookie" -a "ls set clear"
complete -c comic-dl -n "__fish_seen_subcommand_from cache" -a "clear status"
complete -c comic-dl -n "__fish_seen_subcommand_from config" -a "path show init"
complete -c comic-dl -n "__fish_seen_subcommand_from list-sources" -a "--json --plugin"
complete -c comic-dl -n "__fish_seen_subcommand_from help" -a "{commands}"
complete -c comic-dl -n "not __fish_use_subcommand" -a "{lib_flags}"
"""
    raise ValueError(f"unsupported shell: {shell!r} (expected bash, zsh, or fish)")


def _run_completion(argv: list[str]) -> int:
    """Emit a shell-completion script: ``completion <bash|zsh|fish>``."""
    parser = ComicArgumentParser(
        prog="comic-dl completion",
        description="Print a shell completion script for the given shell.",
    )
    parser.add_argument(
        "shell",
        nargs="?",
        choices=["bash", "zsh", "fish"],
        help="shell to generate completion for (bash/zsh/fish)",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    if args.shell is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        console.print(_completion_script(args.shell), end="")
    except ValueError as exc:
        print_error(str(exc))
        return EXIT_USAGE
    return EXIT_OK


async def _run_help(argv: list[str]) -> int:
    """``comic-dl help [COMMAND]`` — styled help for a subcommand or the tool."""
    if not argv:
        print_help()
        return EXIT_OK
    command = argv[0]
    if command == "update":
        return await _run_update(["--help"])
    if command in _LIBRARY_COMMANDS:
        return await asyncio.to_thread(run_library_command, command, ["--help"])
    if command == "list-sources":
        return await _run_list_sources(["--help"])
    if command == "cookie":
        return await asyncio.to_thread(_run_cookie, ["--help"])
    if command == "cache":
        return await asyncio.to_thread(_run_cache, ["--help"])
    if command == "config":
        return await asyncio.to_thread(_run_config, ["--help"])
    if command == "completion":
        return await asyncio.to_thread(_run_completion, ["--help"])
    return _unknown_command(command)


def _is_verbosity_token(token: str) -> bool:
    """True for exact verbosity flags: ``-v``/``-vv``/... or ``--verbose``.

    Only a ``-`` followed solely by ``v`` characters counts, so an option
    that merely contains a ``v`` (e.g. ``--convoy``) is never misread.
    """
    # CLI flag name, not a credential.
    if token == "--verbose":  # nosec B105
        return True
    if token.startswith("-") and not token.startswith("--"):
        body = token[1:]
        return bool(body) and all(c == "v" for c in body)
    return False


@dataclass(frozen=True)
class _GlobalFlags:
    """Global CLI flags resolved in one pass over the raw argv.

    ``argv`` keeps every non-global token in original order — the values of
    ``--color`` / ``--debug-file`` / ``--config`` are consumed, and the flag
    tokens themselves are omitted so per-command parsers never see them.
    ``verbosity`` is the raw ``-v`` / ``--verbose`` count (clamped later by
    :func:`set_verbosity`).
    """

    argv: list[str]
    verbosity: int
    json: bool
    color_mode: str | None
    no_config: bool
    debug_file: str | None
    config_path: str | None


def _scan_global_flags(argv: list[str]) -> _GlobalFlags:
    """Resolve every global CLI flag from ``argv`` in a single left-to-right pass.

    ``--json`` is detected but left in ``argv`` (subcommand parsers declare
    it). ``-q``/``--quiet`` survive only when the leading command is
    ``update`` — those two parsers declare the flag, every other subcommand
    parser rejects it, and the download path re-reads ``sys.argv`` in
    :func:`parse_urls` so it is unaffected by the strip.
    """
    verbosity = 0
    json_flag = False
    no_color = False
    no_config = False
    color_mode: str | None = None
    debug_file: str | None = None
    config_path: str | None = None
    kept: list[str] = []
    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        # Flag names, not credentials.
        if token == "--json":  # nosec B105
            json_flag = True
            kept.append(token)
        elif token == "--no-color":  # nosec B105
            no_color = True
        elif token == "--no-config":  # nosec B105
            no_config = True
        elif token == "--color":  # nosec B105
            if i + 1 < n:
                color_mode = argv[i + 1]
                i += 1
        elif token.startswith("--color="):
            color_mode = token.split("=", 1)[1].strip() or None
        elif token == "--debug-file":  # nosec B105
            if i + 1 < n and not argv[i + 1].startswith("-"):
                debug_file = argv[i + 1]
                i += 1
        elif token.startswith("--debug-file="):
            debug_file = token.split("=", 1)[1].strip() or None
        elif token == "--config":  # nosec B105
            if i + 1 < n and not argv[i + 1].startswith("-"):
                config_path = argv[i + 1]
                i += 1
        elif token.startswith("--config="):
            config_path = token.split("=", 1)[1].strip() or None
        elif _is_verbosity_token(token):
            # CLI flag name, not a credential.
            if token == "--verbose":  # nosec B105
                verbosity += 1
            else:
                verbosity += len(token) - 1
        else:
            kept.append(token)
        i += 1
    if no_color:
        color_mode = "never"
    if not (kept and kept[0].lstrip("-") == "update"):
        kept = [a for a in kept if a not in ("-q", "--quiet")]
    return _GlobalFlags(
        argv=kept,
        verbosity=verbosity,
        json=json_flag,
        color_mode=color_mode,
        no_config=no_config,
        debug_file=debug_file,
        config_path=config_path,
    )


async def main() -> int:
    """CLI entry point: parse argv, dispatch to the right command, return exit code."""
    flags = _scan_global_flags(sys.argv[1:])
    set_verbosity(flags.verbosity)
    set_json_mode(flags.json)
    set_debug_file(flags.debug_file)
    set_config_path(flags.config_path)
    _install_signal_handlers()
    reset_stop()
    atexit.register(_cleanup_temp_dir)
    load_plugins()

    try:
        # Global flags are resolved in main()'s single argv pass above; the
        # per-command and download parsers never see their tokens.
        color_mode = flags.color_mode
        if color_mode not in (None, "auto", "always", "never"):
            err_console.print(
                f"  [bold {ERROR}]error:[/] argument --color: invalid choice: {color_mode!r}"
            )
            return EXIT_USAGE
        apply_color_mode(color_mode)
        if flags.no_config:
            set_no_config()
        argv = flags.argv
        # A recognized command must be the first token. If one appears after
        # other flags, those flags are ones the dispatcher doesn't handle
        # (download-path flags like --impersonate) — falling through would
        # feed the command words into URL parsing and fetch junk hosts.
        # Reject loudly instead of silently misdispatching.
        _known = set(_completion_commands())
        _cmd_pos = next(
            (i for i, a in enumerate(argv) if a in _known), None
        )
        if argv and _cmd_pos is not None and _cmd_pos > 0:
            err_console.print(
                f"  [bold {ERROR}]{glyphs().err}[/] error: unexpected flag(s) "
                f"before command '{argv[_cmd_pos]}'."
            )
            err_console.print(
                f"  [{MUTED}]Put command-specific flags after the command:[/] "
                f"comic-dl {' '.join(argv[_cmd_pos:])}"
            )
            return EXIT_USAGE
        if argv:
            raw_command = argv[0]
            command = raw_command
            if command.startswith("--"):
                command = command[2:]
            if command == "update":
                return await _run_update(argv[1:])
            if command == "help":
                return await _run_help(argv[1:])
            if command == "config":
                return await asyncio.to_thread(_run_config, argv[1:])
            if command == "completion":
                return await asyncio.to_thread(_run_completion, argv[1:])
            if command == "list-sources" or "--list-sources" in argv:
                marker = (
                    argv.index("--list-sources")
                    if "--list-sources" in argv
                    else 0
                )
                return await _run_list_sources(
                    argv[:marker] + argv[marker + 1:]
                )
            if command == "cookie":
                return await asyncio.to_thread(_run_cookie, argv[1:])
            if command == "cache":
                return await asyncio.to_thread(_run_cache, argv[1:])
            if command in _LIBRARY_COMMANDS:
                return await asyncio.to_thread(
                    run_library_command, command, argv[1:]
                )
            if raw_command and not raw_command.startswith("-"):
                return _unknown_command(raw_command)

        urls, args = parse_urls()

        global _RESUME_CMD
        _RESUME_CMD = resume_command()

        worker = asyncio.create_task(_run_urls(urls, args))
        global _WORK_TASK
        _WORK_TASK = worker

        try:
            return await worker
        except asyncio.CancelledError:
            return EXIT_INTERRUPTED
        finally:
            _WORK_TASK = None
            reset_stop()
            await _close_webview_session()
            await close_shared_cover_session()
    except KeyboardInterrupt:
        try:
            teardown_active()
            err_console.print()
            resume_cmd = _RESUME_CMD or ""
            print_interrupt(
                partial=bool(active_partial_files()),
                resume_cmd=resume_cmd,
            )
        except BaseException:
            pass
        return EXIT_INTERRUPTED
    except ComicError as exc:
        return report_error(exc)
    except Exception as exc:
        return report_error(exc, hint="Run again with -vvv for a traceback.")

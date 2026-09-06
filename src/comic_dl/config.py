"""Configuration-file loading and platform config-directory resolution."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from typing import Any

from platformdirs import PlatformDirs

_DIRS = PlatformDirs("comic-dl", appauthor=False)

_RUNTIME_HTTP: dict[str, Any] = {}

_RUNTIME_DOWNLOAD: dict[str, Any] = {}

_CONFIG_PATH_OVERRIDE: str | None = None

_CONFIG_DIR_OVERRIDE: Path | None = None

_NO_CONFIG = False

_LOAD_CACHE: tuple[Path, int, int, dict[str, Any]] | None = None

DEFAULT_CONFIG_TOML = """\
# comic-dl configuration file.
# Location: ~/.config/comic-dl/config.toml (Linux),
#           ~/Library/Application Support/comic-dl/config.toml (macOS),
#           %APPDATA%\\comic-dl\\config.toml (Windows).
#
# Precedence: CLI flag > config file > built-in default.

# Default download directory. CLI -o/--output still overrides this.
output = "~/Downloads/comic-dl"

# Number of parallel page downloads.
concurrency = 5

# Max URLs in flight across a batch file (1-16).
parallel = 5

# Max chapters of a series downloading at once (1-8).
chapter_parallel = 1

# Maximum size per image (accepts 500MB, 2GB, 512KB, or plain bytes).
max_image_size = "100MB"

# Maximum total download size per run (0 = unlimited).
max_size = 0

# HTTP / anti-bot settings. Each key can be overridden per-run by a CLI flag.
[http]
# TLS/HTTP impersonation profile used for requests (--impersonate).
impersonate = "chrome146"

# Cloudflare challenge solver (--solver):
#   auto           detect a challenge, clear the stale cookie, retry once;
#                  solve in the system webview when available, else fall
#                  back to impersonation-only
#   impersonation  never open a webview; rely on the fingerprint profile
#   webview        force the system-webview solver
#   off            never retry challenged requests
solver = "auto"

# Persist session cookies across runs. --no-cookie disables for one run.
# Inspect/clear with: comic-dl cookie ls|clear [HOST]
cookie-jar = true

# On-disk scrape response cache (metadata GETs). --no-cache disables for one
# run; inspect/clear with: comic-dl cache status|clear
cache = true
# Hours a cached scrape response is served without a revalidation request.
cache-ttl = 6
# Total on-disk size budget for cached responses (accepts 100MB, 2GB, 512KB,
# or plain bytes). When the cache exceeds it, the oldest entries are evicted.
cache-max-bytes = "50MB"
# Advisory entry-count ceiling, retained for display; eviction is governed by
# cache-max-bytes and the 14-day hard drop age, not this count.
cache-max-entries = 5000

# Per-site request throttling. --no-rate disables for one run.
rate-enabled = true

# Requests/second per host. Built-in defaults cover kagane.to (1.5),
# kstatic.to (2.0) and e-hentai.org (2.0); add or override hosts here.
rate = { "kagane.to" = 1.5, "kstatic.to" = 2.0, "e-hentai.org" = 2.0 }

# Hard bound on one image download: seconds before an in-flight page fetch is
# abandoned (retries still apply). Raise for slow servers.
download-timeout = 60

# Retries after the first attempt per image (total attempts = this + 1).
# Lower to fail faster on flaky hosts; the shared backoff still paces retries.
download-retries = 2

# Generic fallback scraper: when a URL's host has no dedicated/plugin scraper,
# extract a direct image, a gallery, or a chapter-list series straight from the
# page (static HTML + embedded JSON only). --no-generic disables for one run.
[download]
generic = true

# Scratch space for chapter staging (pages land here before archiving).
# Defaults to the system temp dir; set this to a large stable disk when the
# system temp is small or RAM-backed (many distros mount /tmp as tmpfs).
# tmp-dir = ""

# Archive output. format is the container: cbz (default, most widely supported),
# zip (plain zip, still embeds ComicInfo.xml), or cbt (tar). --format
# overrides for one run.
[archive]
format = "cbz"

# CBZ archive compression. stored (default) writes pages as-is (fastest);
# deflate | deflate:0-9 opts into zlib. Comic pages are already-compressed
# raster, so deflate rarely shrinks the CBZ — enable deliberately.
# Applies to zip-family archives only; cbt is never compressed.
# --compress overrides for one run.
compression = "stored"

# Per-source overrides keyed by host. Host keys must be quoted (a bare
# [sources.kagane.to] would nest into sources.kagane.to).
# Supported keys: rate (req/s), mode (solver mode), impersonate (profile).
# It beats the global [http] rate map and solver setting.
# Example (uncomment to apply):
# [sources."kagane.to"]
# rate = 0.8
# mode = "auto"        # auto | impersonation | webview | off
# impersonate = "chrome146"
"""

# The documented defaults, parsed once at import so :func:`effective_config`
# deep-merges against an object instead of re-parsing the constant per call.
# If the constant ever stops being valid TOML this module refuses to import —
# loud and early, because a broken default would be silent data loss.
_DEFAULT_CONFIG = tomllib.loads(DEFAULT_CONFIG_TOML)


def set_config_path(path: str | Path | None) -> None:
    """Point :func:`config_path` at a custom config.toml (or reset to default).

    ``--config`` and ``$COMIC_DL_CONFIG`` both funnel through here so every
    code path (downloads, ``update``, ``config``, library commands) reads the
    same effective file. Passing ``None`` (or a blank string) restores the
    default location. Relative paths are anchored to the current directory so
    the returned :func:`config_path` is always absolute.
    """
    global _CONFIG_PATH_OVERRIDE, _LOAD_CACHE
    if path is None or (isinstance(path, str) and not path.strip()):
        _CONFIG_PATH_OVERRIDE = None
    else:
        _CONFIG_PATH_OVERRIDE = str(Path(path).expanduser().absolute())
    # The file at the new location needs a fresh parse, not a stale snapshot.
    _LOAD_CACHE = None


def set_config_dir(path: str | Path | None) -> None:
    """Point :func:`config_dir` at ``path`` (tests); ``None`` restores default."""
    global _CONFIG_DIR_OVERRIDE
    _CONFIG_DIR_OVERRIDE = Path(path).expanduser() if path else None


def set_runtime_http(**kwargs: Any) -> None:
    """Override ``[http]`` settings for the current process (from CLI flags)."""
    _RUNTIME_HTTP.update(kwargs)


def set_runtime_download(**kwargs: Any) -> None:
    """Override ``[download]`` settings for the current process (from CLI flags)."""
    _RUNTIME_DOWNLOAD.update(kwargs)


def clear_runtime_http() -> None:
    """Drop every process-wide ``[http]`` override set by :func:`set_runtime_http`.

    Primarily for tests that must not leak flag state into later cases; the
    CLI never calls it because flags persistently describe the run.
    """
    _RUNTIME_HTTP.clear()


def clear_runtime_download() -> None:
    """Drop every process-wide ``[download]`` override.

    Mirror of :func:`clear_runtime_http` for the ``[download]`` table.
    """
    _RUNTIME_DOWNLOAD.clear()


def set_no_config(enabled: bool = True) -> None:
    """Ignore ``config.toml`` for the rest of the process (``--no-config``).

    Pass ``False`` to restore normal loading. While active, :func:`load_config`
    returns ``{}`` so every setting falls back to its built-in default.
    """
    global _NO_CONFIG, _LOAD_CACHE
    _NO_CONFIG = enabled
    _LOAD_CACHE = None


def no_config_active() -> bool:
    """Whether ``config.toml`` is currently ignored (``--no-config``)."""
    return _NO_CONFIG


def http_setting(name: str, default: Any = None, host: str | None = None) -> Any:
    """Effective ``[http] <name>`` value for an optional ``host``.

    Precedence: runtime override (CLI flag) > ``[sources."<host>"]`` table
    (when ``host`` is given) > ``[http]`` > ``default``.
    """
    if name in _RUNTIME_HTTP:
        return _RUNTIME_HTTP[name]
    cfg = load_config()
    if host is not None:
        sources = cfg.get("sources")
        if isinstance(sources, dict):
            table = sources.get(host)
            if isinstance(table, dict) and name in table:
                return table[name]
    http_cfg = cfg.get("http")
    if isinstance(http_cfg, dict) and name in http_cfg:
        return http_cfg[name]
    return default


def download_setting(name: str, default: Any = None) -> Any:
    """Effective ``[download] <name>`` value.

    Precedence: runtime override (CLI flag) > ``[download]`` table > ``default``.
    """
    if name in _RUNTIME_DOWNLOAD:
        return _RUNTIME_DOWNLOAD[name]
    cfg = load_config()
    dl_cfg = cfg.get("download")
    if isinstance(dl_cfg, dict) and name in dl_cfg:
        return dl_cfg[name]
    return default


def generic_enabled() -> bool:
    """Whether the generic fallback scraper may run for the current process."""
    return bool(download_setting("generic", True))


def config_dir() -> Path:
    """Per-platform config directory.

    Linux/macOS/Windows each resolve to the conventional location, e.g.
    ``~/.config/comic-dl`` on Linux. :func:`set_config_dir` overrides it for
    tests and embedded runtimes.
    """
    if _CONFIG_DIR_OVERRIDE is not None:
        return _CONFIG_DIR_OVERRIDE
    return Path(_DIRS.user_config_dir)


def config_path() -> Path:
    """Full path to the ``config.toml`` file (may not exist yet).

    Precedence: ``--config`` (set via :func:`set_config_path`), then
    ``$COMIC_DL_CONFIG``, then the per-platform default location. Always
    absolute, with ``~`` expanded.
    """
    if _CONFIG_PATH_OVERRIDE:
        return Path(_CONFIG_PATH_OVERRIDE)
    env = os.environ.get("COMIC_DL_CONFIG")
    if env and env.strip():
        return Path(env.strip()).expanduser().absolute()
    return config_dir() / "config.toml"


def cache_dir() -> Path:
    """Per-platform cache directory (user cache dir, e.g. ``~/.cache/comic-dl``).

    Holds derived data that can be safely deleted, such as the scrape HTTP
    response cache.
    """
    return Path(_DIRS.user_cache_dir)


def _warn_bad_config(path: Path, exc: Exception) -> None:
    from .ui import print_warning  # lazy: ui imports utils -> config

    print_warning(f"Ignoring unreadable/malformed config file {path}: {exc}")


def load_config() -> dict[str, Any]:
    """Read config.toml into a dict. Never raises.

    A missing or malformed file is treated as an empty config so the tool
    degrades gracefully to defaults. A malformed file also emits a warning
    (``--no-config`` skips the file entirely).

    The file is parsed once and the result is cached against
    ``(path, mtime, size)``: the many per-setting lookups in a run share a
    single parse, and a config edited mid-run does not change behavior until
    :func:`reload_config` is called. A path that is not a regular file (a
    directory, FIFO, ...) is refused with a warning instead of being opened —
    opening a FIFO would block the process forever.
    """
    if _NO_CONFIG:
        return {}
    global _LOAD_CACHE
    path = config_path()
    try:
        st = path.stat()
    except OSError:
        return {}
    if not stat.S_ISREG(st.st_mode):
        _warn_bad_config(path, IsADirectoryError(f"not a regular file: {path}"))
        return {}
    key = (path, st.st_mtime_ns, st.st_size)
    cached = _LOAD_CACHE
    if cached is not None and cached[:-1] == key:
        return cached[-1]
    try:
        # Path.open (io.open) rather than bare open(): config reads must not
        # be affected by a caller swapping builtins.open — e.g. tests that
        # stub write failures for the download pipeline.
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, UnicodeError) as exc:
        _warn_bad_config(path, exc)
        # Cache the failure too: a malformed file must not warn again on every
        # setting lookup in the run, but a fixed file (mtime/size change) gets
        # one fresh parse and, if still broken, one fresh warning.
        _LOAD_CACHE = (*key, {})
        return {}
    data = data if isinstance(data, dict) else {}
    _validate_config(path, data)
    key = (path, st.st_mtime_ns, st.st_size)
    _LOAD_CACHE = (*key, data)
    return data


def reload_config() -> dict[str, Any]:
    """Force a fresh parse of config.toml on the next read.

    Normally :func:`load_config` serves a snapshot cached against the file's
    mtime and size; call this to pick up an edit made mid-run (for example a
    ``comic-dl config`` subcommand that just rewrote the file).
    """
    global _LOAD_CACHE
    _LOAD_CACHE = None
    return load_config()


# Documented keys under each table, used only to warn about typos and wrong
# shapes. Validation is advisory: values the tool cannot use degrade to their
# defaults, matching the "never let a bad config break a run" contract.
_KNOWN_KEYS: dict[str, Any] = {
    "output": "str",
    "concurrency": "int_min1",
    "parallel": "int_1_16",
    "chapter_parallel": "int_1_8",
    "max_image_size": "size",
    "max_size": "size_or_zero",
}

_TABLE_RULES: dict[str, dict[str, Any]] = {
    "[http]": {
        "impersonate": "str",
        "solver": {"auto", "impersonation", "webview", "off"},
        "cookie-jar": "bool",
        "cache": "bool",
        "cache-ttl": "int_min1",
        "cache-max-bytes": "size",
        "cache-max-entries": "int_min1",
        "rate-enabled": "bool",
        "rate": "rate_map",
        "download-timeout": "positive_number",
        "download-retries": "int_min0",
    },
    "[download]": {
        "generic": "bool",
        "tmp-dir": "str",
    },
    "[archive]": {
        "format": {"cbz", "zip", "cbt"},
        "compression": "str",
    },
    "[sources]": {
        "rate": "positive_number",
        "mode": {"auto", "impersonation", "webview", "off"},
        "impersonate": "str",
    },
}

_KNOWN_TABLES = frozenset({"http", "download", "archive", "sources"})


def _validate_config(path: Path, data: dict[str, Any]) -> None:
    """Emit one warning per malformed or unknown key in ``data``.

    Broken values are never mutated or dropped here — consumers already fall
    back to their defaults — but a warning makes a typo or a wrong type
    visible at load time instead of silently producing a different effective
    configuration. Anything that is not a plain ``dict`` (e.g. a bare
    ``toml = "string"`` table value) is reported like a wrong type.
    """
    problems: list[str] = []
    for key, value in data.items():
        if key in _KNOWN_TABLES:
            if not isinstance(value, dict):
                problems.append(f"{key!r}: expected a table, got {value!r}")
            elif key == "sources":
                for host, table in value.items():
                    if isinstance(table, dict):
                        _validate_table("[sources]", table, problems)
                    else:
                        problems.append(
                            f'[sources."{host}"]: expected a table, got {table!r}'
                        )
            else:
                _validate_table(f"[{key}]", value, problems)
        elif isinstance(value, dict):
            problems.append(f"unknown table {key!r}")
        elif key not in _KNOWN_KEYS:
            problems.append(f"unknown key {key!r}")
        else:
            rule = _KNOWN_KEYS[key]
            if not _check_rule(rule, value):
                problems.append(f"{key!r}: expected {_describe(rule)}, got {value!r}")
    if problems:
        from .ui import print_warning  # lazy: ui imports utils -> config

        detail = ", ".join(problems[:6])
        if len(problems) > 6:
            detail += f", ... ({len(problems) - 6} more)"
        print_warning(f"config {path}: {detail}")


def _validate_table(label: str, table: dict[str, Any], problems: list[str]) -> None:
    rules = _TABLE_RULES.get(label)
    if rules is None:
        problems.append(f"{label}: unknown table")
        return
    for key, value in table.items():
        rule = rules.get(key)
        if rule is None:
            problems.append(f"{label} key {key!r} is not recognized")
            continue
        if not _check_rule(rule, value):
            problems.append(
                f"{label} {key!r}: expected {_describe(rule)}, got {value!r}"
            )


def _check_rule(rule: Any, value: Any) -> bool:
    # bool is an int subclass; never let it satisfy the int/float rules.
    if isinstance(value, bool):
        return rule == "bool"
    if rule == "bool":
        return False
    if rule == "str":
        return isinstance(value, str)
    if rule == "int_min1":
        return isinstance(value, int) and value >= 1
    if rule == "int_min0":
        return isinstance(value, int) and value >= 0
    if rule == "int_1_16":
        return isinstance(value, int) and 1 <= value <= 16
    if rule == "int_1_8":
        return isinstance(value, int) and 1 <= value <= 8
    if rule == "positive_number":
        return isinstance(value, (int, float)) and value > 0
    if rule == "size":
        return isinstance(value, (int, str)) and (not isinstance(value, bool))
    if rule == "size_or_zero":
        if isinstance(value, int):
            return value >= 0
        return isinstance(value, str) and value.strip() != ""
    if rule == "rate_map":
        if not isinstance(value, dict):
            return False
        return all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            for v in value.values()
        )
    if isinstance(rule, set):
        return value in rule
    return True


def _describe(rule: Any) -> str:
    if isinstance(rule, set):
        return "one of " + ", ".join(sorted(map(str, rule)))
    return {
        "bool": "a boolean",
        "str": "a string",
        "int_min1": "an integer >= 1",
        "int_min0": "an integer >= 0",
        "int_1_16": "an integer between 1 and 16",
        "int_1_8": "an integer between 1 and 8",
        "positive_number": "a positive number",
        "size": "a size (e.g. 100MB) or a byte count",
        "size_or_zero": "a size or an integer >= 0",
        "rate_map": "a map of host to requests/sec",
    }.get(str(rule), str(rule))


def effective_config() -> dict[str, Any]:
    """Effective configuration: documented defaults deep-merged with config.toml
    and the current run's CLI overrides.

    Values from the file win per key; nested tables (``[http]``, ``[archive]``,
    ``[sources]``) are merged recursively. Process-wide runtime overrides set
    by :func:`set_runtime_http` / :func:`set_runtime_download` are merged on
    top so ``config list`` / ``config show`` reflect what a run would actually
    use, not just the on-disk file.
    """
    eff = _deep_merge(_DEFAULT_CONFIG, load_config())
    if _RUNTIME_HTTP:
        http = eff.get("http")
        http = http if isinstance(http, dict) else {}
        eff["http"] = {**http, **_RUNTIME_HTTP}
    if _RUNTIME_DOWNLOAD:
        dl = eff.get("download")
        dl = dl if isinstance(dl, dict) else {}
        eff["download"] = {**dl, **_RUNTIME_DOWNLOAD}
    return eff


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def default_output_dir() -> Path:
    """Built-in default download location, under the platform's Downloads dir.

    ``<Downloads>/comic-dl`` — on Windows the real Shell Folders value is
    used (see :func:`comic_dl.platform.downloads_dir`).
    """
    from .platform import downloads_dir

    return downloads_dir() / "comic-dl"


def configured_output_dir() -> Path:
    """Effective default output, honouring ``config.toml``'s ``output`` key.

    Falls back to :func:`default_output_dir` when unset. CLI ``-o`` still
    overrides this (handled by the caller).
    """
    out = load_config().get("output")
    if isinstance(out, str) and out.strip():
        return Path(out).expanduser()
    return default_output_dir()

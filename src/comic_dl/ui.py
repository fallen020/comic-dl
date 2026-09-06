"""Rich console UI: progress bars, activity spinners, banners, and glyph sets."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import contextlib
import difflib
import locale
import os
import re
import sys
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TextIO, TypeVar
from urllib.parse import parse_qsl, urlsplit, urlunsplit

if TYPE_CHECKING:
    from _typeshed import SupportsWrite

from rich.color import ColorSystem
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as esc
from rich.padding import Padding
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .errors import EXIT_INTERRUPTED, EXIT_OK, ComicError, DownloadTimeout, ScrapeTimeout
from .utils import normalize_url_key

FINAL_FRAME_DELAY = 0.15

JSON_SCHEMA_VERSION = 1

JSON_MODE = False

NORMAL = 0
VERBOSE = 1
DIAGNOSTIC = 2
TRACE = 3

VERBOSITY = NORMAL


def is_interactive() -> bool:
    """Whether stderr is a real interactive terminal.

    Gates TTY-only output such as the startup banner and interactive pickers.
    Everything a human watches lands on stderr, so that stream decides: when
    output is piped or redirected (scripts, ``2>log``), branding and pickers
    are suppressed and the run degrades to clean batch output.
    """
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


def verbosity() -> int:
    """Current diagnostic verbosity level (0-3)."""
    return VERBOSITY

TAG_HTTP = "http"
TAG_RETRY = "retry"
TAG_SCRAPE = "scrape"
TAG_TIMING = "timing"
TAG_DOWNLOAD = "download"
TAG_WARNING = "warning"
TAG_ERROR = "error"

DIAGNOSTIC_TAGS = (
    TAG_HTTP,
    TAG_RETRY,
    TAG_SCRAPE,
    TAG_TIMING,
    TAG_DOWNLOAD,
    TAG_WARNING,
    TAG_ERROR,
)

_TAG_STYLES = {
    TAG_HTTP: "cyan",
    TAG_RETRY: "yellow",
    TAG_SCRAPE: "magenta",
    TAG_TIMING: "blue",
    TAG_DOWNLOAD: "green",
    TAG_WARNING: "yellow",
    TAG_ERROR: "red",
}


def _http_trace_enabled() -> bool:
    """True when ``COMIC_DL_TRACE_HTTP`` asks for header-level HTTP traces.

    Any value other than an empty string, ``0``, ``false``, ``no`` or ``off``
    turns it on, so ``COMIC_DL_TRACE_HTTP=1`` works as simply as
    ``COMIC_DL_TRACE_HTTP=headers``.
    """
    value = os.environ.get("COMIC_DL_TRACE_HTTP", "").strip().lower()
    return value not in ("", "0", "false", "no", "off")


def set_json_mode(enabled: bool) -> None:
    """Route human result lines (``print_success``/``print_summary``/batch
    summary) to stderr while a ``--json`` run owns stdout.

    The JSON payload is printed directly via ``console``; everything a human
    would normally read must not leak into stdout and corrupt the payload.
    ``--no-color`` and verbosity are set the same way in ``main``.
    """
    global JSON_MODE
    JSON_MODE = enabled


def set_verbosity(level: int) -> None:
    """Set the diagnostic verbosity level, clamped to ``NORMAL``..``TRACE``.

    Level 0 keeps the user UI clean; higher levels add progressively deeper
    diagnostics via :func:`vlog`. ``--debug`` was removed — tracebacks now
    surface at ``TRACE`` (``-vvv``). Both the argv scanner and the argparse
    ``-v`` counter may produce values above ``TRACE``; this remains the final
    clamp.
    """
    global VERBOSITY
    VERBOSITY = max(NORMAL, min(level, TRACE))

_DEBUG_FILE: TextIO | None = None


def set_debug_file(path: str | None) -> None:
    """Route diagnostic logs to ``path`` (or back to the console when ``None``).

    Also forces the verbosity to ``TRACE`` so the file captures the full
    trace; a single notice line is printed to stderr. The file is appended on
    every write and closed at process exit. Parent directories are created
    automatically and the file is opened with owner-only permissions (``0600``)
    so trace logs — which may embed request URLs and cookies — aren't world
    readable.
    """
    global _DEBUG_FILE
    if _DEBUG_FILE is not None:
        with contextlib.suppress(Exception):
            _DEBUG_FILE.close()
        _DEBUG_FILE = None
    if path is None:
        return
    try:
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            handle = os.fdopen(fd, "a", encoding="utf-8")
        except OSError:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
    except OSError as exc:
        print_error(f"Could not open debug file: {path} ({exc})")
        return
    _DEBUG_FILE = handle
    set_verbosity(TRACE)
    atexit.register(_close_debug_file)
    trace(f"debug log → {path}")


def flush_debug_file() -> None:
    """Flush buffered debug output to disk without closing the file.

    Called from the interrupt handler, which hard-exits via ``os._exit`` and
    would otherwise skip the normal ``atexit`` close (and its flush).
    """
    if _DEBUG_FILE is not None:
        with contextlib.suppress(Exception):
            _DEBUG_FILE.flush()


def _close_debug_file() -> None:
    global _DEBUG_FILE
    if _DEBUG_FILE is not None:
        with contextlib.suppress(Exception):
            _DEBUG_FILE.close()
        _DEBUG_FILE = None


def vlog(level: int, message: str, *, tag: str | None = None) -> None:
    """Emit an additive diagnostic line when ``VERBOSITY >= level``.

    Level 0 UI must never be routed through ``vlog`` — the ``print_*``
    functions are the user-facing API and ``vlog`` only adds context
    (``VERBOSE``), diagnosis (``DIAGNOSTIC``) or internals (``TRACE``).

    Diagnostic lines always go to stderr, so ``--json`` stdout payloads
    stay clean. ``tag`` must be one of :data:`DIAGNOSTIC_TAGS` so ``-vv``
    output stays searchable and consistent. Each tag renders in its own
    color (:data:`_TAG_STYLES`) so a stream of diagnostics stays scannable.
    """
    if level > VERBOSITY:
        return
    if _DEBUG_FILE is not None:
        prefix = f"[{tag}] " if tag is not None else ""
        _DEBUG_FILE.write(f"{prefix}{message}\n")
        _DEBUG_FILE.flush()
        return
    if tag is not None:
        style = _TAG_STYLES.get(tag, "dim")
        body = f"[{style}]{esc(f'[{tag}]')}[/] [{MUTED}]{esc(message)}[/]"
    else:
        body = f"[{MUTED}]{esc(message)}[/]"
    _active_console().print(body)


def stage_line(text: str) -> None:
    """Emit a lifecycle-stage step (``Fetching chapter…``) at ``-v``.

    Plain and tagless at ``VERBOSE`` (-v) so the run's narrative reads
    naturally; from ``DIAGNOSTIC`` (-vv) upward the same step carries the
    ``[scrape]`` tag so it stays scannable next to other diagnostics.
    No-op at NORMAL verbosity.
    """
    if VERBOSITY >= DIAGNOSTIC:
        vlog(VERBOSE, text, tag=TAG_SCRAPE)
    else:
        vlog(VERBOSE, text)


def trace(message: str) -> None:
    """Emit a tagless workflow-internal line at ``-vvv`` (TRACE).

    Where ``-vv`` shows *what* happened at the network level, these lines
    show how the pipeline *works* — routing, index decisions, retries,
    resume, archiving. Values are always safe to display (URLs passed
    through :func:`redact_url`, no cookie/token content).
    """
    vlog(TRACE, message)


_REDACT_HEADERS = frozenset({
    "cookie",
    "set-cookie",
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-integrity-token",
    "x-csrf-token",
    "x-auth-token",
    "cf_clearance",
    "token",
})

_REDACT_QUERY_PARAMS = frozenset({
    "token",
    "access_token",
    "auth_token",
    "api_key",
    "apikey",
    "key",
    "cf_clearance",
    "secret",
    "signature",
    "sig",
    "code",
    "password",
})

_HEADER_VALUE_MAX = 200


def _mask_header_value(name: str, value: str) -> str:
    return "***" if name.lower() in _REDACT_HEADERS else value


def _truncate_value(value: str, limit: int = _HEADER_VALUE_MAX) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + glyphs().ellipsis


def redact_url(url: str) -> str:
    """Mask sensitive query parameters (``token=…``) for *display* only.

    The displayed URL never carries credentials, while the real fetch URL
    (built before this function) is unaffected. Non-sensitive URLs pass
    through unchanged. Parameter order and encoding are preserved.
    """
    if "?" not in url:
        return url
    parsed = urlsplit(url)
    parts = [
        (
            f"{k}={v}"
            if k.lower() not in _REDACT_QUERY_PARAMS
            else f"{k}=***"
        )
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit(parsed._replace(query="&".join(parts)))


_HTTP_KEEP_HEADERS = frozenset({
    "accept-ranges",
    "cache-control",
    "cf-cache-status",
    "cf-ray",
    "content-length",
    "content-type",
    "date",
    "etag",
    "last-modified",
    "location",
})


def _filter_headers(headers: Mapping[str, str]) -> list[str]:
    """Whitelist ``headers`` to the trace-relevant set, redacted and
    truncated, as ``"key: value"`` lines ready for one-per-line output."""
    out: list[str] = []
    for key, value in headers.items():
        if key.lower() not in _HTTP_KEEP_HEADERS:
            continue
        if not isinstance(value, str):
            value = str(value)
        out.append(f"{key}: {_mask_header_value(key, _truncate_value(value))}")
    return out


def _print_header_block(headers: list[str], indent: int = 3) -> None:
    """Print a header block one line per header, indented, never wrapped
    mid-token.

    Header values are short after the noise filter (content-length, etag,
    cf-ray, location, …), so each line comfortably fits a terminal column;
    ``no_wrap`` guarantees a long value is never broken inside a token.
    """
    console_obj = _active_console()
    pad = " " * indent
    body = Text(f"{pad}headers:", style=MUTED)
    for line in headers:
        body.append("\n" + pad + "  ")
        body.append(line, style="dim")
    if len(body.plain) > len(pad):
        console_obj.print(body, no_wrap=True, overflow="ignore", crop=False)


def http_event(
    method: str,
    url: str,
    *,
    status: str | int = "",
    duration: float | None = None,
    headers: Mapping[str, str] | None = None,
    error: str = "",
    level: int = DIAGNOSTIC,
    note: str = "",
) -> None:
    """Emit one HTTP request/response line with per-request timing.

    The request line is logged at ``level`` (``DIAGNOSTIC`` for page/cover
    fetches, ``TRACE`` for per-image fetches) and carries the elapsed wall
    time when ``duration`` is given. When the request failed before a
    response, ``error`` replaces ``status`` on the line. ``note`` carries a
    stable user-facing identifier (e.g. ``page_0001.webp``) so the line can be
    cross-referenced against retry/verify logs regardless of URL shape.

    Response ``headers``, when shown, are appended to the *same* line as
    collapsed ``key: value`` pairs — reachable at ``TRACE`` verbosity, or
    earlier when ``COMIC_DL_TRACE_HTTP`` is set — so full observability
    never costs one stdout line per header. Sensitive headers are masked
    and the displayed URL has credentials query-params redacted.
    """
    display_url = redact_url(url)
    line = (
        f"{method} {glyphs().dash} {error} {glyphs().dash} {display_url}"
        if error
        else f"{method} {status} {display_url}"
    )
    if note:
        line += f"  [{note}]"
    if duration is not None:
        line += f"  ({duration * 1000:.0f} ms)"
    if headers and (_http_trace_enabled() or VERBOSITY >= TRACE):
        trace_level = level if _http_trace_enabled() else TRACE
        vlog(trace_level, line, tag=TAG_HTTP)
        kept = _filter_headers(headers)
        if kept:
            _print_header_block(kept)
    else:
        vlog(level, line, tag=TAG_HTTP)


def _resolve_env_color() -> str | None:
    """Resolve the color mode from the environment alone.

    Precedence (Prompt.8 decision (c)): ``NO_COLOR`` > ``CLICOLOR_FORCE`` >
    ``CLICOLOR`` > ``FORCE_COLOR`` > ``TERM=dumb``. Returns ``"never"``,
    ``"always"``, or ``None`` to defer to Rich's TTY auto-detection. Rich
    honors ``NO_COLOR``/``FORCE_COLOR`` at construction but treats
    ``FORCE_COLOR=0`` as a terminal and ignores ``CLICOLOR*``; this function
    closes both gaps.
    """
    if os.environ.get("NO_COLOR", "") != "":
        return "never"
    if os.environ.get("CLICOLOR_FORCE", "") != "":
        return "always"
    clicolor = os.environ.get("CLICOLOR")
    if clicolor == "0":
        return "never"
    if clicolor == "1":
        return "always"
    force = os.environ.get("FORCE_COLOR")
    if force is not None:
        return "never" if force == "0" else "always"
    if os.environ.get("TERM", "").lower() in ("dumb", "unknown"):
        return "never"
    return None


_FORCE_COLOR_SYSTEMS: dict[str, ColorSystem] = {
    "1": ColorSystem.STANDARD,
    "2": ColorSystem.EIGHT_BIT,
    "3": ColorSystem.TRUECOLOR,
}


def _pin_console_color(console_obj: Console, mode: str) -> None:
    """Pin one console to the resolved color mode.

    Rich has no public setter for the terminal flag or color system, so the
    stored ``_force_terminal`` / ``_color_system`` attributes are mutated
    directly (they are plain instance attributes). ``"never"`` also forces
    ``is_terminal`` off so TTY-only renderers that check it stay disabled.
    """
    if mode == "never":
        console_obj.no_color = True
        console_obj._force_terminal = False
        return
    if mode == "always":
        console_obj.no_color = False
        console_obj._force_terminal = True
        depth = os.environ.get("FORCE_COLOR")
        if depth in _FORCE_COLOR_SYSTEMS:
            console_obj._color_system = _FORCE_COLOR_SYSTEMS[depth]
        else:
            console_obj._color_system = console_obj._detect_color_system()
        return
    # auto: restore Rich's own detection (NO_COLOR is read at construction).
    console_obj.no_color = os.environ.get("NO_COLOR", "") != ""
    console_obj._force_terminal = None
    console_obj._color_system = console_obj._detect_color_system()


def apply_color_mode(mode: str | None) -> None:
    """Apply the color mode to both consoles.

    ``mode`` is ``"never"``, ``"always"``, or ``None``/``"auto"`` to resolve
    from the environment and then the TTY. ``--json`` output stays plain under
    every mode: the payload is printed as unstyled text, and ``always`` is
    ignored so the JSON highlighter can never inject ANSI into it.
    """
    if JSON_MODE:
        mode = "never"
    elif mode is None or mode == "auto":
        resolved = _resolve_env_color()
        if resolved is not None:
            mode = resolved
    for c in (console, err_console):
        _pin_console_color(c, mode or "auto")


def set_no_color(enabled: bool) -> None:
    """Force plain output on both consoles (``--no-color``)."""
    apply_color_mode("never" if enabled else None)


@dataclass(frozen=True)
class _GlyphSet:
    """Every non-ASCII symbol the UI prints, plus its ASCII fallback.

    A terminal (or pipe) that cannot emit UTF-8 gets the ASCII column instead
    of mojibake. Selection happens once at import: ``COMIC_DL_ASCII=1`` forces
    ASCII, otherwise UTF-8 is used only when stdout, stderr, and the locale
    default all claim UTF-8 (errors land on stderr, so it must count too).
    """
    arrow: str
    ok: str
    fail: str
    err: str
    success: str
    skip: str
    warn: str
    dash: str
    ndash: str
    radio_on: str
    radio_off: str
    bullet: str
    ellipsis: str
    new: str
    up: str
    down: str
    cursor: str
    bar_fill: str
    bar_empty: str
    dot: str


_UTF8_GLYPHS = _GlyphSet(
    arrow="▸",
    ok="✓",
    fail="✗",
    err="✘",
    success="✔",
    skip="○",
    warn="⚠",
    dash="—",
    ndash="\u2013",
    radio_on="●",
    radio_off="○",
    bullet="•",
    ellipsis="…",
    new="↓",
    up="↑",
    down="↓",
    cursor="▏",
    bar_fill="█",
    bar_empty="░",
    dot="·",
)

_ASCII_GLYPHS = _GlyphSet(
    arrow=">",
    ok="OK",
    fail="FAIL",
    err="FAIL",
    success="OK",
    skip="-",
    warn="!",
    dash="--",
    ndash="-",
    radio_on="*",
    radio_off="-",
    bullet="-",
    ellipsis="...",
    new="+",
    up="^",
    down="v",
    cursor="|",
    bar_fill="#",
    bar_empty="-",
    dot=".",
)


def _encoding_supports_utf8(encoding: str | None) -> bool:
    if encoding is None:
        return True
    return encoding.replace("-", "").lower() in ("utf8", "utf8sig")


def _ascii_fallback_enabled() -> bool:
    if os.environ.get("COMIC_DL_ASCII") == "1":
        return True
    if os.environ.get("TERM", "").lower() == "dumb":
        return True
    return not all(
        _encoding_supports_utf8(enc)
        for enc in (
            sys.stdout.encoding,
            sys.stderr.encoding,
            locale.getpreferredencoding(False),
        )
    )


_GLYPHS: _GlyphSet = _ASCII_GLYPHS if _ascii_fallback_enabled() else _UTF8_GLYPHS


def glyphs() -> _GlyphSet:
    """The active glyph set, chosen once at import time."""
    return _GLYPHS


def set_ascii_glyphs(enabled: bool) -> None:
    """Force or clear the ASCII glyph set (used by tests and CI)."""
    global _GLYPHS, SPINNER_GLYPHS
    _GLYPHS = _ASCII_GLYPHS if enabled else _UTF8_GLYPHS
    SPINNER_GLYPHS = _SPINNER_GLYPHS_ASCII if enabled else _SPINNER_GLYPHS_UTF8


_SPINNER_GLYPHS_UTF8 = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_GLYPHS_ASCII = "|/-\\"
SPINNER_GLYPHS = _SPINNER_GLYPHS_ASCII if _ascii_fallback_enabled() else _SPINNER_GLYPHS_UTF8
SPIN_INTERVAL = 0.08
SPIN_REFRESH_RATE = 12
SPIN_HEARTBEAT = 3.0

_MIN_BYTES_DISPLAY = 512 * 1024
_MIN_SPEED_ELAPSED = 2.0


_STALL_AFTER = 8.0
_EWMA_ALPHA = 0.3
_SPIKE_FACTOR = 5.0

_UI_GATE: asyncio.Semaphore | None = None
_UI_GATE_LOOP: object | None = None


def get_ui_gate() -> asyncio.Semaphore:
    """A process-global semaphore serializing animated UI (one at a time).

    The series flow can start several ``Progress``/``Live`` renderers on the
    *same* Console concurrently, and those independent Live threads fighting
    one console can wedge the renderer. This gate lets them run (network) in
    parallel but only ever renders one spinner/progress bar to the console.
    """
    global _UI_GATE, _UI_GATE_LOOP
    loop = asyncio.get_running_loop()
    if _UI_GATE is None or _UI_GATE_LOOP is not loop:
        _UI_GATE = asyncio.Semaphore(1)
        _UI_GATE_LOOP = loop
    return _UI_GATE

_COLOR_ROLES: dict[str, str] = {
    "brand": "bright_yellow",  # ANSI 93 — amber brand (banner, bars, spinners)
    "accent": "bright_cyan",  # ANSI 96 — interactive accents (cursors, pickers)
    "success": "green",  # ANSI 32
    "error": "red",  # ANSI 31
    "warning": "yellow",  # ANSI 33 — shares the brand family; the ⚠ glyph disambiguates
    "info": "cyan",  # ANSI 36
    "muted": "bright_black",  # ANSI 90 — the theme's grey slot (dark-mode default)
    "bar_complete": "bright_yellow",
    "bar_finished": "green",
    "bar_track": "bright_black",
}

_LIGHT_ROLE_OVERRIDES: dict[str, str] = {
    "muted": "grey37",
}


def _detect_light_background() -> bool:
    """True when the terminal reports a light background via ``COLORFGBG``.

    ``COLORFGBG`` is ``fg;bg`` color indices; a background index of 8 or more
    means a light background. Absent or unparseable defaults to dark — the
    palette is dark-oriented, and the worst contrast failures only get hit when
    a terminal explicitly signals light (COLORFGBG is the opt-in).
    """
    raw = os.environ.get("COLORFGBG", "")
    try:
        _, bg = raw.split(";")
    except ValueError:
        return False
    return bg.strip().isdigit() and int(bg) >= 8


_LIGHT_BACKGROUND = _detect_light_background()


def _color_token(role: str) -> str:
    """Resolve a semantic role to its color name, honoring light overrides."""
    if _LIGHT_BACKGROUND:
        return _LIGHT_ROLE_OVERRIDES.get(role, _COLOR_ROLES[role])
    return _COLOR_ROLES[role]


def style(role: str, *, bold: bool = False) -> str:
    """Resolve a semantic role to a Rich style string (``"bold <color>"``)."""
    token = _color_token(role)
    return f"bold {token}" if bold else token


BRAND = _color_token("brand")
SUCCESS = _color_token("success")
ERROR = _color_token("error")
WARNING = _color_token("warning")
MUTED = _color_token("muted")
INFO = _color_token("info")

BANNER_PATH = Path(__file__).parent / "banner.txt"

_BANNER_LINE_STYLE = style("brand", bold=True)

console = Console(theme=Theme({
    "brand": _color_token("brand"),
    "success": _color_token("success"),
    "error": _color_token("error"),
    "warning": _color_token("warning"),
    "muted": _color_token("muted"),
    "info": _color_token("info"),
}))

err_console = Console(
    stderr=True,
    theme=Theme({
        "brand": _color_token("brand"),
        "success": _color_token("success"),
        "error": _color_token("error"),
        "warning": _color_token("warning"),
        "muted": _color_token("muted"),
        "info": _color_token("info"),
    }),
)


def suggest(word: str, candidates: list[str]) -> str | None:
    """Best fuzzy match for ``word`` among ``candidates``, if any.

    Ranks by longest common prefix (right for ``--flag`` typos, where
    difflib gets confused by the shared leading dashes), falling back to
    difflib for short words without a shared prefix.
    """
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda c: (_lcp(word, c), difflib.SequenceMatcher(None, word, c).ratio()),
    )
    if _lcp(word, best) >= 3:
        return best
    matches = difflib.get_close_matches(word, candidates, n=1, cutoff=0.6)
    if matches:
        return matches[0]
    lower = word.lower()
    for cand in candidates:
        if cand.lower() == lower:
            return cand
    return None


def _lcp(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


class ComicArgumentParser(argparse.ArgumentParser):
    """argparse parser whose usage errors carry a "Did you mean" hint.

    Overrides ``error()`` to keep the usage line on stderr, add a fuzzy
    suggestion when the offending token resembles a known flag, and exit
    with code 2 (invalid CLI usage).
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        hint = self._suggest_for(message)
        err_console.print(f"  [bold {ERROR}]{glyphs().err}[/] error: {esc(message)}")
        if hint:
            err_console.print(f"  [{MUTED}]Did you mean:[/] {esc(hint)}?")
        raise SystemExit(2)

    def print_help(self, file: SupportsWrite[str] | None = None) -> None:
        print_parser_help(self)

    def _suggest_for(self, message: str) -> str | None:
        tokens = _option_tokens(message)
        if not tokens:
            return None
        known = list(self._option_string_actions)
        for token in tokens:
            hint = suggest(token, known)
            if hint is not None:
                return hint
        for token in tokens:
            sibling = SIBLING_CMDS.get(token)
            if sibling is not None:
                return sibling
        return None


def _option_tokens(message: str) -> list[str]:
    """Extract likely ``--flag``/``-f`` tokens from an argparse error text."""
    return re.findall(r"--[A-Za-z0-9][A-Za-z0-9-]*|-[A-Za-z0-9]", message)

SIBLING_CMDS: dict[str, str] = {
    "--latest": "`--latest` is a separate command; try `comic-dl latest -o <dir>`",
    "--list": "`--list` is a separate command; try `comic-dl list -o <dir>`",
    "--info": "`--info` is a separate command; try `comic-dl info -o <dir> <title>`",
    "--list-sources": "`--list-sources` is a separate command; try `comic-dl list-sources`",
}

_ACTIVE_LIVE: Live | None = None
_ACTIVE_SNAPSHOT: Callable[[], str] | None = None


def register_active(live: Live | None, snapshot: Callable[[], str]) -> None:
    """Register the live console and snapshot callback for ``Ctrl+R`` refresh."""
    global _ACTIVE_LIVE, _ACTIVE_SNAPSHOT
    _ACTIVE_LIVE = live
    _ACTIVE_SNAPSHOT = snapshot


def unregister_active() -> None:
    """Clear the registered live console."""
    global _ACTIVE_LIVE, _ACTIVE_SNAPSHOT
    _ACTIVE_LIVE = None
    _ACTIVE_SNAPSHOT = None


def active_snapshot() -> str:
    """Best-effort one-line progress summary of whatever is rendering now."""
    if _ACTIVE_SNAPSHOT is not None:
        try:
            return _ACTIVE_SNAPSHOT()
        except Exception:
            return ""
    return ""


def _active_console() -> Console:
    """Diagnostics console, safe to write *while a Live is rendering*.

    All progress, banners, headers, and other transient output belongs on
    stderr (``err_console``). Rich's ``Live`` render loop is not safe against
    a concurrent ``print`` on the same Console — the render thread and the
    print race over cursor/clear sequences and the spinner frame can freeze.
    While a Live is active (``register_active``), route output through the
    Live's own console, which rich buffers and redraws cleanly above the live
    region. Final results (see ``print_success``/``print_summary``/tables)
    bypass this and write to ``console`` (stdout) directly.
    """
    live = _ACTIVE_LIVE
    if live is not None:
        return live.console
    return err_console


def teardown_active() -> None:
    """Synchronously clear the active Live. Safe to call from a signal handler."""
    live = _ACTIVE_LIVE
    if live is not None:
        with contextlib.suppress(Exception):
            live.stop()
    unregister_active()


def print_banner() -> None:
    """Print the application banner."""
    _active_console().print()
    try:
        raw_lines = BANNER_PATH.read_text().splitlines()
    except (OSError, PermissionError):
        raw_lines = []

    styled_parts: list[tuple[str, str]] = []
    for line in raw_lines:
        if not line:
            continue
        styled_parts.append((line + "\n", _BANNER_LINE_STYLE))

    if styled_parts:
        logo = Text.assemble(*styled_parts)
        logo.no_wrap = True
        _active_console().print(logo, justify="center", overflow="ignore", crop=False)

    _active_console().print(
        Text("Made For Enthusiast By Enthusiast", style=f"italic {MUTED}"),
        justify="center",
    )
    _active_console().print()


def print_header(text: str, *, console_obj: Console | None = None) -> None:
    """Print a section header line."""
    (console_obj or _active_console()).print(
        f"  [bold brand]{glyphs().arrow}[/] [bold]{esc(text)}[/]"
    )


def print_meta(label: str, value: str, *, console_obj: Console | None = None) -> None:
    """Print a key/value metadata line."""
    (console_obj or _active_console()).print(
        f"    [{MUTED}]{esc(label)}:[/] [white]{esc(value)}[/]"
    )


def print_success(message: str) -> None:
    """Print a success message line."""
    target = err_console if JSON_MODE else console
    target.print(f"  [bold {SUCCESS}]{glyphs().success}[/] {esc(message)}")


def print_skipped(message: str) -> None:
    """Print a skipped message line."""
    _active_console().print(f"  [{MUTED}]{glyphs().skip}[/] {esc(message)}")


def print_error(message: str) -> None:
    """Print an error message line to stderr."""
    err_console.print(f"  [bold {ERROR}]{glyphs().err}[/] {esc(message)}")


def print_error_block(headline: str, details: list[str], *, limit: int = 5) -> None:
    """Print one aligned error block: a ``✘`` headline line with the detail
    lines indented underneath so the whole message reads as a unit.

    ``details`` are emitted without the per-line glyph and with deeper
    indentation than ``print_error``, so a long list (e.g. pages that failed
    verification) stays visually grouped with its summary instead of being
    flush at column 0.
    """
    err_console.print(f"  [bold {ERROR}]{glyphs().err}[/] {esc(headline)}")
    shown = details[:limit]
    for line in shown:
        err_console.print(f"      {esc(line)}")


def print_partial_block(
    url: str,
    *,
    missing: int,
    total: int,
    output_dir: str | Path,
) -> None:
    """The single aligned conclusion for a partial/unsuccessful download.

    Replaces the old scattered ``⚠ Partial download…`` + ``✘ Failed: …`` pair
    with one exit block that answers *what / why / what to do*: the URL, the
    missing count, and a consistent ``rerun to resume`` hint on stderr.
    """
    err_console.print(
        f"  [bold {ERROR}]{glyphs().warn}[/] [white]{esc(url)}[/] {glyphs().dash} "
        f"partial download: {missing} of {total} pages missing."
    )
    err_console.print(
        f"      rerun to resume: comic-dl -u {esc(url)} -o {esc(str(output_dir))}"
    )


def print_warning(message: str) -> None:
    """Print a warning message line to stderr."""
    err_console.print(f"  [bold {WARNING}]{glyphs().warn}[/] {esc(message)}")


def print_interrupt(
    progress: str = "",
    *,
    partial: bool = False,
    resume_cmd: str = "",
) -> None:
    """The single aligned interrupt conclusion (Ctrl-C / SIGINT).

    Merges the old two-line ``⚠ Interrupted.`` + ``… partial save kept`` pair
    into one message with a consistent ``rerun to resume`` hint, so an
    interrupted run reads as a unit and never contradicts itself. When nothing
    was partially saved (``partial=False``) the resume hint is omitted.
    When ``resume_cmd`` is provided, the hint echoes the real command the user
    ran instead of a hardcoded example.
    """
    tail = f" ({progress})" if progress else ""
    err_console.print(
        f"  [bold {WARNING}]{glyphs().warn}[/] [bold]Interrupted.{tail}[/]"
    )
    if partial:
        hint = resume_cmd if resume_cmd else "comic-dl -u <url> -o <dir>"
        err_console.print(
            "      [white]Partial save kept[/] "
            f"{glyphs().dash} rerun to resume: "
            f"[muted]{esc(hint)}[/]"
        )


def print_url(url: str) -> None:
    """Print a URL line."""
    _active_console().print(f"  [{INFO}]{esc(url)}[/]")


def print_dim(message: str, *, console_obj: Console | None = None) -> None:
    """Print a muted helper line."""
    (console_obj or _active_console()).print(f"  [{MUTED}]{esc(message)}[/]")


def print_retry(attempt: int, total: int, *, reason: str = "") -> None:
    """Print a retry notice with attempt counters and an optional reason."""
    msg = f"Retrying ({attempt}/{total})"
    if reason:
        msg += f" after {esc(reason)}"
    vlog(DIAGNOSTIC, msg, tag=TAG_RETRY)


def print_error_detail(context: str, reason: str, *, hint: str = "") -> None:
    """Print an error with its cause and an optional remediation hint."""
    err_console.print(
        f"  [bold {ERROR}]{glyphs().err}[/] [white]{esc(context)}[/]"
    )
    err_console.print(f"    [{MUTED}]reason:[/] {esc(reason)}")
    if hint:
        err_console.print(f"    [{MUTED}]hint:[/] {esc(hint)}")


def _classify(exc: BaseException) -> tuple[str, int]:
    """Map an exception to a (friendly message, exit code) pair."""
    if isinstance(exc, ScrapeTimeout):
        return f"Request timed out after {exc.timeout:.0f}s fetching {exc.url}", 1
    if isinstance(exc, DownloadTimeout):
        return f"Download timed out after {exc.timeout:.0f}s ({exc.filename})", 1
    if isinstance(exc, ComicError):
        return exc.message, exc.exit_code
    module = type(exc).__module__ or ""
    if module.startswith("curl_cffi") or isinstance(exc, ConnectionError):
        return "Network error. Check your internet connection.", 1
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc}", 1
    return "Unexpected internal error.", 1


def error_kind(exc: BaseException) -> str:
    """Stable machine-readable category for ``exc``.

    Mirrors :func:`_classify`'s branches so JSON-mode and library consumers
    can branch on a taxonomy instead of parsing message text. Kinds:
    ``usage``/``scrape``/``download``/``timeout``/``library`` for
    :class:`~comic_dl.errors.ComicError` subclasses, then ``network``,
    ``os``, and ``internal``.
    """
    if isinstance(exc, ComicError):
        return getattr(exc, "kind", "error")
    module = type(exc).__module__ or ""
    if module.startswith("curl_cffi") or isinstance(exc, ConnectionError):
        return "network"
    if isinstance(exc, OSError):
        return "os"
    return "internal"


def report_error(
    exc: BaseException, *, context: str = "", hint: str = ""
) -> int:
    """Print a user-friendly error for ``exc`` and return its exit code.

    A traceback is printed only at ``TRACE`` verbosity (``-vvv``; see
    :func:`set_verbosity`). User-actionable details are always preserved in
    the message.
    """
    message, code = _classify(exc)
    if context:
        print_error_detail(context, message, hint=hint)
    else:
        print_error(message)
        if hint:
            err_console.print(f"    [{MUTED}]hint:[/] {esc(hint)}")
    if VERBOSITY >= TRACE:
        import traceback

        traceback.print_exception(exc)
    return code


def print_batch_summary(
    succeeded: int,
    skipped: int,
    failed: int,
    failures: list[str],
    *,
    chapters: int = 0,
    total_bytes: int = 0,
    elapsed_secs: float = 0,
    failure_details: list[tuple[str, str]] | None = None,
) -> None:
    """Print the end-of-run tally across all URLs.

    ``failure_details`` (``(label, reason)`` pairs) replaces the flat per-URL
    error list with a grouped, deduped recap so a batch with many identical
    failures reads as one unit instead of N repeated lines.
    """
    total = succeeded + skipped + failed
    if not failures:
        if skipped:
            print_success(f"All {total} URLs completed successfully ({skipped} skipped).")
        else:
            print_success(f"All {total} URLs completed successfully.")
    else:
        print_dim(
            f"Processed {total} URLs: {succeeded} downloaded, "
            f"{skipped} skipped, {failed} failed"
        )
        if failure_details:
            print_failure_recap(failure_details)
        else:
            for url in failures:
                print_error(url)
    if chapters or total_bytes:
        _console = _active_console()
        url_word = "URL" if total == 1 else "URLs"
        chapter_word = "chapter" if chapters == 1 else "chapters"
        parts: list[str] = [
            f"Batch complete: {total} {url_word}, {chapters} {chapter_word} downloaded"
        ]
        if failed:
            parts.append(f"{failed} failed")
        if total_bytes:
            parts.append(format_bytes(total_bytes))
        if elapsed_secs > 0 and total_bytes > 0:
            mb = total_bytes / 1024 / 1024
            parts.append(f"{mb / elapsed_secs:.1f} MB/s")
        _console.print(
            "  [bold]" + f"  {glyphs().bullet}  ".join(parts) + "[/]"
        )


def print_summary(
    series_title: str,
    downloaded: int,
    skipped: int,
    failed: int,
    output_dir: str,
    elapsed: str,
    total_bytes: int = 0,
    elapsed_secs: float = 0,
    partial: int = 0,
    interrupted: bool = False,
) -> None:
    """Print the final per-series download summary block.

    The verdict line tells the truth: ``Download complete`` only when nothing
    failed, is incomplete, or was cut short by an interrupt. Partial chapters
    (saved but missing pages) are their own category, never folded into
    ``failed``.
    """
    _console = err_console if JSON_MODE else console
    _console.print()
    if interrupted:
        _console.print(
            f"  [bold {WARNING}]{glyphs().warn}[/] [bold]Interrupted[/]"
        )
    elif failed or partial:
        if partial and not failed:
            verdict = "Download incomplete"
        else:
            verdict = "Download completed with errors"
        _console.print(f"  [bold {ERROR}]{glyphs().err}[/] [bold]{verdict}[/]")
    else:
        _console.print(f"  [bold {SUCCESS}]{glyphs().success}[/] [bold]Download complete[/]")
    _console.print()
    _console.print(f"    [{MUTED}]Series     :[/] [white]{esc(series_title)}[/]")
    chapters_word = "chapter" if downloaded == 1 else "chapters"
    brief = ""
    if partial and failed:
        brief = f"  [yellow]({partial} partial, {failed} failed)[/]"
    elif partial:
        brief = f"  [yellow]({partial} partial)[/]"
    elif failed:
        brief = f"  [yellow]({failed} failed)[/]"
    _console.print(f"    [{MUTED}]Downloaded :[/] [white]{downloaded} {chapters_word}[/]{brief}")
    if skipped:
        _console.print(f"    [{MUTED}]Skipped    :[/] [white]{skipped} chapter(s)[/]")
    if total_bytes:
        size_str = format_bytes(total_bytes)
        if elapsed_secs > 0 and downloaded > 0:
            mb = total_bytes / 1024 / 1024
            throughput = mb / elapsed_secs
            _console.print(
                f"    [{MUTED}]Total      :[/] [white]{size_str}[/] "
                f"[white]in[/] [white]{elapsed}[/] "
                f"[white]({throughput:.2f} MB/s)[/]"
            )
        else:
            _console.print(f"    [{MUTED}]Size       :[/] [white]{size_str}[/]")
    _console.print(f"    [{MUTED}]Saved to   :[/] [white]{esc(output_dir)}[/]")
    _console.print()


def _batch_eta(
    states: list[RowState],
    done: int,
    batch_total: int,
    nbytes: int,
    speed: float,
) -> float | None:
    """Remaining-time projection for the batch Overall line.

    Weights the projection by pages rather than URL count so one huge chapter
    (e.g. a 158-page gallery) does not swing the ETA every tick. Falls back to
    the URL-count projection when page counts are unknown. Returns ``None``
    when no sane projection exists (nothing done, batch complete, or absurd
    estimates).
    """
    if done <= 0 or batch_total <= done:
        return None
    done_pages = sum(st.total for st in states if st.status == "done" and st.total > 0)
    all_pages = sum(st.total for st in states if st.total > 0)
    if done_pages > 0 and all_pages > done_pages:
        est_total = nbytes / done_pages * all_pages
    else:
        est_total = nbytes / done * batch_total
    eta = (est_total - nbytes) / speed
    return eta if 0 < eta < 24 * 3600 else None


def _format_remaining(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "00:00"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class ETA(ProgressColumn):
    """Progress column rendering estimated time remaining for a task."""

    def render(self, task):
        total = task.total
        completed = task.completed
        elapsed = task.elapsed
        if not total or completed <= 0 or elapsed is None or elapsed <= 0:
            return Text("--:--", style=MUTED)
        remaining = elapsed / completed * (total - completed)
        if remaining <= 0:
            return Text("")
        return Text(_format_remaining(remaining), style=BRAND)


def _indent_renderable(renderable: Any, width: int = 4) -> Any:
    """Indent a live-area line (progress bar / timing) so it aligns under the
    row header's text instead of hugging the terminal's left edge."""
    return Padding(renderable, (0, 0, 0, width))


def _format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec >= 1024 ** 3:
        return f"{bytes_per_sec / 1024 ** 3:.1f} GB/s"
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / 1024 / 1024:.1f} MB/s"
    if bytes_per_sec >= 100 * 1024:
        return f"{bytes_per_sec / 1024:.0f} KB/s"
    if bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


# Fixed-width numeric slots for live regions. Numbers are right-aligned to a
# constant column width so a row never shifts left/right between frames as the
# value changes (e.g. ``"2.0 MB/s"`` must not become ``"1234 KB/s"`` a tick
# later). All live usage goes through these; ``format_bytes`` (scrollback,
# summaries) keeps its compact form.
def format_bytes_fixed(n: int, width: int = 8) -> str:
    """Byte count right-aligned to a fixed ``width`` column."""
    return format_bytes(n).rjust(width)


def format_speed_fixed(bytes_per_sec: float) -> str:
    """Speed right-aligned to 10 columns (max ``999.9 MB/s`` = 10 wide)."""
    return _format_speed(bytes_per_sec).rjust(10)


def format_remaining_fixed(seconds: float) -> str:
    """ETA right-aligned to 8 columns (``mm:ss`` or up to ``99:59:59``)."""
    return _format_remaining(seconds).rjust(8)


def format_elapsed_fixed(secs: float) -> str:
    """Elapsed clock ``( 12.3s)`` with a stable column width."""
    return f"({secs:5.1f}s)"


def _stall_seconds(state: RowState) -> float:
    if state.status != "running":
        return 0.0
    ref = state.last_tick or state.started_at
    if not ref:
        return 0.0
    return time.monotonic() - ref


def _row_eta_remaining(
    st: RowState | None, task: Any
) -> float | None:
    """Estimated seconds left for one download row, or ``None`` when unknown.

    Pages-based rate once a page completes; a bytes-based estimate before the
    first page finishes (each remaining page assumed ≈ the partial first page),
    so an ETA appears as soon as bytes start flowing.
    """
    total = task.total
    completed = task.completed
    if (
        total
        and not task.finished
        and completed > 0
        and task.elapsed is not None
        and task.elapsed > 0
    ):
        remaining = task.elapsed / completed * (total - completed)
        if remaining > 0:
            return remaining
    if (
        total
        and not task.finished
        and completed == 0
        and st is not None
        and st.bytes >= _MIN_BYTES_DISPLAY
        and st.speed_ewma > 0
    ):
        remaining = (total - 1) * st.bytes / st.speed_ewma
        if remaining > 0:
            return remaining
    return None


def _apply_ewma(st: RowState, n: int, now: float) -> None:
    """Accumulate bytes and update the spike-filtered EWMA speed for a row."""
    if n <= 0:
        return
    dt = now - st.bytes_last_tick if st.bytes_last_tick else 0.0
    st.bytes += n
    st.last_tick = now
    if dt > 0:
        inst = n / dt
        if st.speed_ewma <= 0:
            st.speed_ewma = inst
        elif inst <= st.speed_ewma * _SPIKE_FACTOR:
            st.speed_ewma = _EWMA_ALPHA * inst + (1 - _EWMA_ALPHA) * st.speed_ewma
    st.bytes_last_tick = now


@dataclass(slots=True)
class RowState:
    """Single source of truth for one live status row.

    ``status`` is one of ``"queued"`` | ``"running"`` | ``"done"``. Page/byte
    counts and the transient activity string all live here so the renderer,
    the batch overall line, and the completed section derive from one model
    instead of a set of parallel dictionaries.
    """

    key: str
    label: str = ""
    stage: str = ""
    status: str = "running"
    done: int = 0
    total: int = 0
    bytes: int = 0
    speed_ewma: float = 0.0
    last_tick: float = 0.0
    bytes_last_tick: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    ok: bool | None = None
    result: str = ""
    pages: int = 0


class _RowStatsColumn(ProgressColumn):
    """Bytes / speed / ETA suffix for one download row.

    Each metric is hidden until it is meaningful (see ``_MIN_BYTES_DISPLAY``
    and ``_MIN_SPEED_ELAPSED``), so the line never shows a placeholder like
    ``0.0 MB/s`` or ``ETA --:--``.
    """

    def __init__(self, state_getter: Callable[[], RowState | None] | None = None):
        self._get = state_getter
        super().__init__()

    def render(self, task):
        st = self._get() if self._get is not None else None
        # A stalled row's bytes/speed/ETA are stale by definition: the last
        # known rate no longer means anything, so hide the whole readout and
        # let the "waiting for server…" clause carry the state instead.
        if st is not None and _stall_seconds(st) >= _STALL_AFTER:
            return Text("")
        parts: list[tuple[str, str]] = []
        if st is not None and st.bytes >= _MIN_BYTES_DISPLAY:
            elapsed = time.monotonic() - st.started_at if st.started_at else 0.0
            parts.append((f"  {glyphs().bullet}  ", MUTED))
            parts.append((format_bytes_fixed(st.bytes), "white"))
            if elapsed >= _MIN_SPEED_ELAPSED:
                speed = st.speed_ewma if st.speed_ewma > 0 else st.bytes / elapsed
                parts.append((f"  {glyphs().bullet}  ", MUTED))
                parts.append((format_speed_fixed(speed), BRAND))
        remaining = _row_eta_remaining(st, task)
        if remaining is not None and remaining > 0:
            parts.append((f"  {glyphs().bullet}  ETA ", MUTED))
            parts.append((format_remaining_fixed(remaining), BRAND))
        return Text.assemble(*parts) if parts else Text("")


def _truncate_label(text: str, max_width: int = 0) -> str:
    """Clip ``text`` to ``max_width`` columns (or a share of the console)."""
    width = max_width or max(int(err_console.width * 0.6), 20)
    if len(text) <= width:
        return text
    return text[: width - 1] + glyphs().ellipsis


def _mini_bar(done: int, total: int, width: int = 16) -> str:
    if total <= 0:
        return glyphs().bar_empty * width
    filled = round(done / total * width)
    return glyphs().bar_fill * filled + glyphs().bar_empty * (width - filled)


def _format_duration(secs: float) -> str:
    """Compact wall-clock duration, e.g. ``1m42s`` / ``3h 5m``."""
    total = int(secs)
    hours, rem = divmod(total, 3600)
    minutes, secs_ = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs_}s"
    return f"{secs_}s"


def _running_row_renderable(
    state: RowState,
    progress: Progress | None,
    frame: int,
) -> Group:
    """Two-line render for one live running row (header + optional bar)."""
    glyph = SPINNER_GLYPHS[frame % len(SPINNER_GLYPHS)]
    header = Text.assemble((f"  {glyph} ", f"bold {BRAND}"))
    # No key fallback: row keys are internal ids ("main") and must never
    # surface as user-facing text. An unlabeled row just renders the stage.
    label = state.label
    if label:
        header.append(_truncate_label(label), style="bold white")
    stalled = _stall_seconds(state) >= _STALL_AFTER
    if stalled:
        # The stage is a lie while stalled: nothing is downloading. Replace it
        # with a single honest clause instead of appending a second,
        # contradicting one ("Downloading images…  waiting for server… 43s").
        # The seconds are right-aligned so the clause keeps a fixed width as
        # the stall counter grows.
        header.append(
            f"  {glyphs().bullet}  waiting for server{glyphs().ellipsis} "
            f"{int(_stall_seconds(state)):3d}s",
            style=MUTED,
        )
    elif state.stage and state.stage != label:
        if label:
            header.append(f"  {glyphs().bullet}  ", style=MUTED)
        header.append(
            _truncate_label(
                state.stage, max_width=max(int(err_console.width * 0.35), 16)
            ),
            style=MUTED,
        )
    parts: list[Any] = [header]
    if progress is not None and progress.tasks:
        parts.append(_indent_renderable(progress))
    return Group(*parts)


def make_download_progress(
    state_getter: Callable[[], RowState | None] | None = None,
) -> Progress:
    """Build the live progress renderer used for a download run."""
    return Progress(
        BarColumn(
            complete_style=style("bar_complete"),
            finished_style=style("bar_finished"),
            pulse_style=style("bar_complete"),
            style=style("bar_track"),
        ),
        TextColumn(" {task.completed}/{task.total} pages", style=MUTED),
        _RowStatsColumn(state_getter),
        console=err_console,
        auto_refresh=False,
    )


def make_spinner() -> Progress:
    """Build a standalone indeterminate spinner."""
    return Progress(
        SpinnerColumn(spinner_name="dots", style=BRAND),
        TextColumn("{task.description}"),
        console=err_console,
        transient=True,
    )


_STATUS_T = TypeVar("_STATUS_T")


async def run_with_status(
    desc: str, coro: Coroutine[Any, Any, _STATUS_T]
) -> _STATUS_T:
    """Await ``coro`` behind a live brand spinner.

    Interactive terminals get a smooth braille glyph plus a running elapsed
    timer; the final frame is held as a brief ✔ (or ✘ on error) so the line
    doesn't vanish abruptly. Non-interactive (piped) output prints a muted
    heartbeat only after ``SPIN_HEARTBEAT`` seconds, then every ~3 s, so a
    long run never looks frozen.

    The caller owns the UI gate (``get_ui_gate``) so only one renderer talks
    to the console at a time.
    """
    start = time.monotonic()
    status: tuple[str, str] = (glyphs().success, SUCCESS)
    live: Live | None = None
    if err_console.is_terminal:
        live = Live(
            Text(""),
            console=err_console,
            refresh_per_second=SPIN_REFRESH_RATE,
            auto_refresh=False,
            transient=True,
        )
        live.start()
        register_active(live, lambda: desc)

    done = asyncio.Event()
    driver = asyncio.create_task(_drive_status(desc, start, live, done))
    try:
        try:
            result = await coro
        except BaseException:
            status = (glyphs().err, f"bold {ERROR}")
            raise
        return result
    finally:
        done.set()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await driver
        if live is not None:
            glyph, style = status
            live.update(Text.assemble(
                ("  ", ""),
                (glyph, style),
                (" ", ""),
                (desc, "bold white"),
                (f"  ({time.monotonic() - start:.1f}s)", MUTED),
            ))
            live.refresh()
            await asyncio.sleep(FINAL_FRAME_DELAY)
            live.stop()
        unregister_active()


async def _drive_status(
    desc: str,
    start: float,
    live: Live | None,
    done: asyncio.Event,
) -> None:
    frame = 0
    next_beat = SPIN_HEARTBEAT
    while not done.is_set():
        elapsed = time.monotonic() - start
        try:
            if live is not None:
                live.update(Text.assemble(
                    (f"  {SPINNER_GLYPHS[frame % len(SPINNER_GLYPHS)]} ", "bold brand"),
                    (desc, "white"),
                    (f"  {format_elapsed_fixed(elapsed)}", MUTED),
                ), refresh=True)
            elif elapsed >= next_beat:
                # Non-TTY (piped/child) heartbeat: always ASCII, never braille —
                # the pipe reader may be on any locale and braille would mojibake.
                glyph = _SPINNER_GLYPHS_ASCII[frame % len(_SPINNER_GLYPHS_ASCII)]
                _active_console().print(
                    f"  [{glyph}] [{MUTED}]{desc} {glyphs().ellipsis} "
                    f"{elapsed:5.1f}s[/]"
                )
                next_beat += SPIN_HEARTBEAT
        except Exception:
            # A single render failure must never kill the driver and freeze a
            # spinner while downloads continue: keep the last good frame and
            # carry on to the next tick.
            vlog(VERBOSE, "status render failed; keeping last frame")
        frame += 1
        if not done.is_set():
            await asyncio.sleep(SPIN_INTERVAL)


def _decode_key(data: bytes) -> str:
    """Map a raw terminal key byte sequence to a canonical key name."""
    if data == b"\x1b[A":
        return "up"
    if data == b"\x1b[B":
        return "down"
    if data == b"\x1b[D":
        return "left"
    if data == b"\x1b[C":
        return "right"
    if data == b" ":
        return "space"
    if data in (b"\r", b"\n"):
        return "enter"
    if data == b"\x03":
        return "ctrl-c"
    if data == b"\x1b":
        return "esc"
    if data in (b"\x7f", b"\x08"):
        return "backspace"
    try:
        ch = data.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"
    if len(ch) == 1 and ch.isprintable():
        return ch
    return "unknown"


def _make_key_reader() -> Callable[[], str]:
    """Return a callable that reads one key from the terminal in raw mode."""
    if os.name == "nt":
        import msvcrt

        _WINDOWS_ARROW = {"H": "up", "P": "down", "K": "left", "M": "right"}

        def _read_windows() -> str:
            ch = msvcrt.getwch()  # type: ignore[attr-defined]
            if ch in ("\x00", "\xe0"):
                return _WINDOWS_ARROW.get(msvcrt.getwch(), "unknown")  # type: ignore[attr-defined]
            if ch == "\r":
                return "enter"
            if ch == "\x03":
                return "ctrl-c"
            if ch in ("\x08", "\x7f"):
                return "backspace"
            if ch == "\x1b":
                return "esc"
            if ch == " ":
                return "space"
            return ch.lower()

        return _read_windows

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()

    def _read_posix() -> str:
        with contextlib.ExitStack() as stack:
            old = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            stack.callback(termios.tcsetattr, fd, termios.TCSADRAIN, old)
            first = os.read(fd, 1)
            if first == b"":
                # stdin hit EOF (terminal closed). Signal the prompt so it can
                # stop instead of spinning forever decoding empty input.
                raise EOFError
            if first == b"\x1b":
                ready, _, _ = select.select([fd], [], [], 0.05)
                if ready:
                    return _decode_key(b"\x1b" + os.read(fd, 2))
                return "esc"
            return _decode_key(first)

    return _read_posix


def _checkbox_renderable(
    title: str,
    options: list[tuple[int, str]],
    checked: set[int],
    cursor: int,
    view_start: int,
    height: int,
) -> Group:
    """Render one frame of the checkbox selector."""
    lines: list[Any] = [
        Text(f"  {title}", style=f"bold {BRAND}", no_wrap=True, overflow="ellipsis")
    ]
    for offset, (num, label) in enumerate(options[view_start : view_start + height]):
        index = view_start + offset
        selected = num in checked
        is_cursor = index == cursor
        row_style = "bold white" if is_cursor else "white"
        glyph_style = SUCCESS if selected else MUTED
        lines.append(Text.assemble(
            ("  ", ""),
            (f"{glyphs().arrow} " if is_cursor else "  ", ""),
            (glyphs().radio_on if selected else glyphs().radio_off, glyph_style),
            ("  ", ""),
            (f"{num}. ", MUTED),
            (label, row_style),
            no_wrap=True,
            overflow="ellipsis",
        ))
    lines.append(Text(
        f"  {glyphs().up}/{glyphs().down} move "
        f"{glyphs().dot} space toggle {glyphs().dot} a=all {glyphs().dot} "
        f"enter ok {glyphs().dot} q quit {glyphs().dot} ctrl-c interrupt",
        style=MUTED,
        no_wrap=True,
        overflow="ellipsis",
    ))
    return Group(*lines)


def checkbox_prompt(
    title: str,
    options: list[tuple[int, str]],
    *,
    checked: set[int] | None = None,
    read_key: Callable[[], str] | None = None,
    console_obj: Console | None = None,
    height: int = 10,
) -> set[int] | None:
    """Interactive multi-select with a modern checkbox look.

    Renders ``options`` as ``(number, label)`` pairs on ``● N. label`` /
    ``○ N. label`` rows inside a Rich ``Live`` and reads raw keys:

      ↑/↓ or j/k  move the cursor
      space        toggle the row under the cursor
      a            select every row
      enter        confirm and return the checked numbers
      q / Esc      cancel and return ``None``
      Ctrl-C       interrupt the run (raises ``KeyboardInterrupt``)

    ``q``/``Esc`` are a clean cancel (nothing chosen); ``Ctrl-C`` stays an
    interrupt so the caller's exit code keeps meaning "interrupted", exactly
    like :func:`source_search`.

    The default ``read_key`` reads from ``sys.stdin`` in raw mode (POSIX
    ``termios``/``tty``, Windows ``msvcrt``); tests inject a scripted
    reader. Returns the 1-based numbers of every checked row, or ``None``
    when the user cancels. The caller owns any surrounding renderer
    (e.g. an :class:`Activity` pause/resume).
    """
    if not options:
        return set()
    numbers = [num for num, _ in options]
    n = len(numbers)
    if height < 3:
        height = 3
    height = min(height, n)
    selected = set(checked or ()) & set(numbers)
    cursor = 0
    view_start = 0
    target = console_obj or console

    if read_key is None:
        read_key = _make_key_reader()

    live = Live(
        Text(""),
        console=target,
        refresh_per_second=SPIN_REFRESH_RATE,
        transient=False,
        vertical_overflow="visible",
    )
    live.start()
    register_active(live, lambda: f"{title} ({len(selected)}/{n} selected)")
    try:
        while True:
            live.update(_checkbox_renderable(
                title, options, selected, cursor, view_start, height,
            ))
            try:
                key = read_key()
            except EOFError:
                # stdin closed mid-selection: treat it as a cancel so the
                # prompt cannot busy-loop decoding empty input.
                return None
            if key in ("down", "j"):
                cursor = min(cursor + 1, n - 1)
                if cursor >= view_start + height:
                    view_start = cursor - height + 1
            elif key in ("up", "k"):
                cursor = max(cursor - 1, 0)
                if cursor < view_start:
                    view_start = cursor
            elif key == "space":
                num = numbers[cursor]
                if num in selected:
                    selected.discard(num)
                else:
                    selected.add(num)
            elif key == "a":
                selected = set(numbers)
            elif key == "enter":
                return selected
            elif key in ("q", "esc"):
                return None
            elif key == "ctrl-c":
                raise KeyboardInterrupt
    finally:
        live.stop()
        unregister_active()


@dataclass(slots=True)
class SourceRow:
    """Display-ready source row (decoupled from the scraper registry)."""

    domain: str
    name: str
    capabilities: tuple[str, ...]
    origin: str
    version: str = ""


def _source_row_text(row: SourceRow) -> str:
    return " ".join((row.domain, row.name, *row.capabilities, row.origin)).lower()


def _filter_source_rows(rows: list[SourceRow], query: str) -> list[int]:
    q = query.lower()
    return [i for i, r in enumerate(rows) if q in _source_row_text(r)]


def render_sources_table(
    rows: list[SourceRow],
    *,
    console_obj: Console | None = None,
    note: bool = True,
) -> None:
    """Print the supported-sources table (non-interactive path).

    Columns: SOURCE · CAPABILITIES · ORIGIN. Shared by ``--list-sources``
    and the unsupported-URL error listing so the two never drift apart.
    Defaults to stdout (``--list-sources`` is a result); error-path callers
    pass ``err_console``.
    """
    target = console_obj or console
    target.print(
        f"  [bold brand]{glyphs().arrow}[/] [bold]Supported sources ({len(rows)})[/]"
    )
    table = Table(
        box=None,
        show_header=True,
        header_style=f"bold {INFO}",
        pad_edge=False,
        padding=(0, 2),
    )
    table.add_column("SOURCE")
    table.add_column("ORIGIN")
    for row in rows:
        table.add_row(row.domain, row.origin)
    target.print(table)
    if note:
        target.print(
            Text(
                "  Third-party sources install as entry-point plugins "
                f"{glyphs().dash} see docs/usage/plugins.md.",
                style=MUTED,
            )
        )


def _source_search_renderable(
    rows: list[SourceRow],
    matches: list[int],
    query: str,
    cursor: int,
    view_start: int,
    height: int,
) -> Group:
    lines: list[Any] = [
        Text(
            f"  Supported sources ({len(rows)})",
            style=f"bold {BRAND}",
            no_wrap=True,
            overflow="ellipsis",
        )
    ]
    domain_width = max((len(r.domain) for r in rows), default=0)
    if matches:
        for offset, idx in enumerate(matches[view_start : view_start + height]):
            row = rows[idx]
            is_cursor = view_start + offset == cursor
            lines.append(Text.assemble(
                ("  ", ""),
                (f"{glyphs().arrow} " if is_cursor else "  ", ""),
                (row.domain.ljust(domain_width), "bold white" if is_cursor else "white"),
                (f"   {row.origin}", MUTED),
                no_wrap=True,
                overflow="ellipsis",
            ))
    else:
        lines.append(Text(
            f"  No matches for \"{query}\"",
            style=MUTED,
            no_wrap=True,
            overflow="ellipsis",
        ))
    lines.append(Text.assemble(
        ("  ", ""),
        (f"/ {query}", "bold white"),
        (glyphs().cursor, INFO),
        no_wrap=True,
        overflow="ellipsis",
    ))
    if query:
        filtered = len(rows) - len(matches)
        lines.append(Text(
            f"  {filtered} filtered out",
            style=MUTED,
            no_wrap=True,
            overflow="ellipsis",
        ))
    lines.append(Text(
        f"  {glyphs().up}{glyphs().down} move {glyphs().dot} type to filter "
        f"{glyphs().dot} esc clear {glyphs().dot} q quit",
        style=MUTED,
        no_wrap=True,
        overflow="ellipsis",
    ))
    return Group(*lines)


def source_search(
    rows: list[SourceRow],
    *,
    read_key: Callable[[], str] | None = None,
    console_obj: Console | None = None,
    height: int = 12,
) -> int:
    """Interactive live search over supported sources (TTY only).

    The bottom line is an always-on filter: every printable character narrows
    the visible rows in real time. There is no selection step — this is a
    discovery view. Arrow keys scroll; ``esc`` clears the filter; ``q`` quits
    cleanly (``EXIT_OK``); ``Ctrl-C`` interrupts (``EXIT_INTERRUPTED``).
    """
    if not rows:
        return EXIT_OK
    if height < 3:
        height = 3
    height = min(height, len(rows))
    query = ""
    matches = list(range(len(rows)))
    cursor = 0
    view_start = 0
    target = console_obj or console

    if read_key is None:
        read_key = _make_key_reader()

    live = Live(
        Text(""),
        console=target,
        refresh_per_second=SPIN_REFRESH_RATE,
        transient=False,
        vertical_overflow="visible",
    )
    live.start()
    register_active(live, lambda: f"Sources: {len(matches)}/{len(rows)}")
    try:
        while True:
            live.update(_source_search_renderable(
                rows, matches, query, cursor, view_start, height,
            ))
            try:
                key = read_key()
            except EOFError:
                return EXIT_OK
            if key == "q":
                return EXIT_OK
            if key == "ctrl-c":
                return EXIT_INTERRUPTED
            if key == "esc":
                if query:
                    query = ""
                    matches = list(range(len(rows)))
                    cursor = 0
                    view_start = 0
                continue
            if key == "backspace":
                if not query:
                    continue
                query = query[:-1]
                matches = _filter_source_rows(rows, query)
                cursor = 0
                view_start = 0
                continue
            if key in ("up", "down"):
                if not matches:
                    continue
                cursor = max(cursor - 1, 0) if key == "up" else min(
                    cursor + 1, len(matches) - 1,
                )
                if cursor < view_start:
                    view_start = cursor
                elif cursor >= view_start + height:
                    view_start = cursor - height + 1
                continue
            if key in ("enter", "left", "right", "unknown"):
                continue
            if len(key) == 1:
                query += key
                matches = _filter_source_rows(rows, query)
                cursor = 0
                view_start = 0
    finally:
        live.stop()
        unregister_active()


class Pipeline:
    """Standalone live spinner UI for a single running download.

    A lighter alternative to the multi-row live table: renders one spinner
    line and swaps in a progress bar once the size becomes known.
    """

    def __init__(self, quiet: bool = False):
        self._quiet = quiet
        self._live: Live | None = None
        # A sensible default so the very first frame is never blank: the Live
        # starts rendering immediately on enter, before the first stage() call.
        self._desc = f"Preparing{glyphs().ellipsis}"
        self._frame = 0
        self._done = False
        self._started: float | None = None
        self._progress: Progress | None = None
        self._spin_task: asyncio.Task | None = None
        self._bytes = 0
        self._state: RowState | None = None

    async def __aenter__(self):
        if not self._quiet:
            await get_ui_gate().acquire()
            self._started = time.monotonic()
            self._live = Live(
                Text(""),
                console=err_console,
                refresh_per_second=SPIN_REFRESH_RATE,
                auto_refresh=False,
                transient=True,
            )
            self._live.start()
            register_active(self._live, self.snapshot)
            self._spin_task = asyncio.create_task(self._spin())
        return self

    async def __aexit__(self, *args):
        self.clear_progress()
        self._done = True
        if self._live:
            self._live.stop()
        unregister_active()
        if self._spin_task is not None:
            self._spin_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._spin_task
            self._spin_task = None
        if not self._quiet:
            get_ui_gate().release()

    def snapshot(self) -> str:
        if self._progress is not None and self._progress.tasks:
            task = self._progress.tasks[0]
            if task.total:
                return f"Downloaded {task.completed}/{task.total} images"
        return self._desc

    def _make_renderable(self):
        header = Text.assemble(
            (self._spinner_text(), f"bold {BRAND}"),
            (self._desc, "bold white"),
        )
        if self._progress is not None:
            parts: list[Any] = [header, _indent_renderable(self._progress)]
            return Group(*parts)
        return Group(header)

    def _spinner_text(self) -> str:
        return f"  {SPINNER_GLYPHS[self._frame % len(SPINNER_GLYPHS)]} "

    async def _spin(self):
        while not self._done:
            if self._live is not None:
                try:
                    self._live.update(self._make_renderable(), refresh=True)
                except Exception:
                    # Never let one bad render kill the loop and freeze the
                    # spinner while downloads keep running.
                    vlog(VERBOSE, "pipeline render failed; keeping last frame")
            self._frame += 1
            await asyncio.sleep(SPIN_INTERVAL)

    def stage(self, desc: str) -> None:
        if desc != self._desc:
            stage_line(desc)
        self._desc = desc
        if self._state is not None:
            self._state.stage = desc
            self._state.last_tick = time.monotonic()

    def show_progress(self, total: int) -> None:
        if self._quiet or total <= 0:
            return
        if self._state is None:
            self._state = RowState(key="main", total=total, started_at=time.monotonic())
        else:
            self._state.total = total
            if not self._state.started_at:
                self._state.started_at = time.monotonic()
        self._progress = make_download_progress(state_getter=lambda: self._state)
        self._progress.add_task("  ", total=total)

    def update_progress(self, done: int) -> None:
        if self._progress is None or not self._progress.tasks:
            return
        task = self._progress.tasks[0]
        total = task.total
        clamped = int(min(done, total)) if total and total > 0 else 0
        self._progress.update(task.id, completed=clamped)
        if self._state is not None:
            self._state.done = clamped
            self._state.last_tick = time.monotonic()

    def clear_progress(self) -> None:
        if self._progress is None:
            return
        self._progress = None

    def add_bytes(self, n: int) -> None:
        """Accumulate downloaded bytes for the live bytes/speed readout."""
        if n > 0:
            self._bytes += n
            if self._state is not None:
                _apply_ewma(self._state, n, time.monotonic())

    def set_activity(self, text: str) -> None:
        """Update the status description without emitting a lifecycle line."""
        self._desc = text
        if self._state is not None:
            self._state.stage = text
            self._state.last_tick = time.monotonic()

    async def close(self) -> None:
        self.clear_progress()
        self._done = True
        if self._spin_task is not None:
            self._spin_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._spin_task
            self._spin_task = None
        if self._live:
            self._live.stop()
            self._live = None
        unregister_active()

    async def _finalize(self, glyph: str, glyph_style: str, message: str) -> None:
        """Stop the Live on the terminal frame for ``glyph + message`` and
        unregister, so a spinner can never keep painting after the run ends."""
        self.clear_progress()
        self._done = True
        if self._spin_task is not None:
            self._spin_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._spin_task
            self._spin_task = None
        await asyncio.sleep(FINAL_FRAME_DELAY)
        if self._live:
            self._live.update(Text.assemble(
                ("  ", ""),
                (glyph, glyph_style),
                (" ", ""),
                (message, "bold white"),
            ))
            self._live.refresh()
            self._live.stop()
            self._live = None
        unregister_active()

    async def succeed(self, message: str) -> None:
        await self._finalize(glyphs().success, f"bold {SUCCESS}", message)

    async def fail(self, message: str) -> None:
        await self._finalize(glyphs().err, f"bold {ERROR}", message)


class _RowSink:
    """Per-row handle handed to a consumer (e.g. :class:`DownloadPipeline`) so
    it can drive one row of an :class:`Activity` without owning the Live.

    ``show_progress`` / ``update_progress`` keep a compact progress bar in the
    row (same style as the standalone ``Pipeline``); ``stage`` swaps the row's
    trailing status text in place. ``succeed`` / ``fail`` retire the row into
    the completed section and print a durable line.
    """

    __slots__ = ("_activity", "_key")

    def __init__(self, activity: Activity, key: str):
        self._activity = activity
        self._key = key

    def stage(self, text: str) -> None:
        self._activity.set_status(self._key, text)
        if self._activity.note_stage(self._key, text):
            stage_line(text)

    def set_activity(self, text: str) -> None:
        self._activity.set_activity(self._key, text)

    def set_label(self, text: str) -> None:
        self._activity.set_label(self._key, text)

    def show_progress(self, total: int) -> None:
        self._activity.show_progress(self._key, total)

    def update_progress(self, done: int) -> None:
        self._activity.update_progress(self._key, done)

    def add_bytes(self, n: int) -> None:
        self._activity.add_bytes(self._key, n)

    def clear_progress(self) -> None:
        self._activity.clear_progress(self._key)

    async def succeed(self, message: str) -> None:
        self._activity.finish_row(self._key, ok=True, message=message)
        self._activity.refresh_now()
        self._print_durable(
            f"  [bold {SUCCESS}]{glyphs().success}[/] {esc(message)}"
        )

    async def fail(self, message: str) -> None:
        self._activity.finish_row(self._key, ok=False, message=message)
        self._activity.refresh_now()
        self._print_durable(f"  [bold {ERROR}]{glyphs().err}[/] {esc(message)}")

    def _print_durable(self, markup: str) -> None:
        """Emit a result line that must survive in scrollback.

        While the Activity's Live region is up, the line must go through the
        Live's own console — a plain stdout write races the stderr renderer,
        displacing the frame and leaving stale spinner/ETA copies behind (the
        "Saved:" glued to the spinner, phantom repeated Overall lines). When
        no Live is active, fall back to the plain printers.
        """
        if _ACTIVE_LIVE is not None:
            _active_console().print(markup)
        elif JSON_MODE:
            err_console.print(markup)
        else:
            console.print(markup)


class Activity:
    """One persistent Live for the whole run: a continuous spinner glyph in
    front of a status/phase string that is replaced in place as the run
    progresses, with an optional per-row progress bar.

    Rows are keyed strings (usually ``"main"`` for the top-level phase plus
    one row per active chapter) and are stacked in insertion order. The Live is
    opened once and never restarted between phases — phase text just swaps via
    ``set_status``, so there is no flicker and no gap. Durable results (per
    chapter "Saved" lines, the run summary) are printed to stdout
    (``console``) as normal scrollback output, safely — the Live renders on
    stderr, so the two streams never race.

    Each row is backed by a :class:`RowState`. A batch run can pre-create
    ``"queued"`` rows (``add_queued_row``), flip them to ``"running"`` as work
    starts (``mark_running``), and retire them to the completed section
    (``finish_row``); :meth:`_render` shows an Overall header, the running
    rows, and the completed section.

    Meant to be used with ``async with act:`` so the Live is always torn down
    (also on exceptions / SIGINT via ``register_active``).
    """

    def __init__(self, quiet: bool = False):
        self._quiet = quiet
        self._live: Live | None = None
        self._frame = 0
        self._done = False
        self._paused = False
        self._dirty = False
        self._started: float | None = None
        self._spin_task: asyncio.Task | None = None
        self._rows: dict[str, RowState] = {}
        self._progress: dict[str, Progress] = {}
        self._order: list[str] = []
        self._logged_stages: dict[str, str] = {}
        self._batch_total: int | None = None

    async def __aenter__(self) -> Activity:
        if not self._quiet:
            await get_ui_gate().acquire()
            self._started = time.monotonic()
            self._live = Live(
                Text(""),
                console=err_console,
                refresh_per_second=SPIN_REFRESH_RATE,
                auto_refresh=False,
                transient=True,
            )
            self._live.start()
            register_active(self._live, self.snapshot)
            self._spin_task = asyncio.create_task(self._spin())
        return self

    async def __aexit__(self, *args) -> None:
        self._done = True
        if self._spin_task is not None:
            self._spin_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._spin_task
            self._spin_task = None
        if self._live is not None:
            self._live.stop()
        unregister_active()
        if not self._quiet:
            get_ui_gate().release()

    def snapshot(self) -> str:
        for key in self._order:
            st = self._rows.get(key)
            if st is not None and st.status == "running":
                return st.stage or st.label or st.key or ""
        return ""

    def pause(self) -> None:
        """Temporarily hide the live renderer (e.g. for an interactive prompt).

        Stops the Live and unregisters it so an interactive widget can render
        to the console without racing the spinner; :meth:`resume` restarts it.
        Row state is preserved across the pause.
        """
        if self._live is not None:
            self._live.stop()
        self._live = None
        unregister_active()
        self._paused = True

    def resume(self) -> None:
        """Restart the live renderer after :meth:`pause`."""
        self._paused = False
        if not self._quiet:
            self._live = Live(
                Text(""),
                console=err_console,
                refresh_per_second=SPIN_REFRESH_RATE,
                auto_refresh=False,
                transient=True,
            )
            self._live.start()
            register_active(self._live, self.snapshot)
            self._update()

    # ── row API ────────────────────────────────────────────────

    def row(self, key: str) -> _RowSink:
        if key not in self._rows:
            self._rows[key] = RowState(key=key, started_at=time.monotonic())
            self._order.append(key)
            self._update()
        return _RowSink(self, key)

    def add_queued_row(self, key: str, label: str = "") -> None:
        """Pre-create a row in the queue so the Overall header can count it
        before its task actually starts."""
        if key in self._rows:
            return
        self._rows[key] = RowState(key=key, label=label, status="queued")
        self._order.append(key)
        self._update()

    def mark_running(self, key: str, stage: str = "") -> None:
        """Flip a ``queued`` row to ``running`` (creating it if missing)."""
        st = self._rows.get(key)
        if st is None:
            st = RowState(key=key, stage=stage, started_at=time.monotonic())
            self._rows[key] = st
            self._order.append(key)
        elif st.status == "queued":
            st.status = "running"
            st.started_at = time.monotonic()
            if stage:
                st.stage = stage
        elif stage:
            st.stage = stage
        self._update()

    def begin_batch(self, total: int) -> None:
        """Set the batch denominator shown in the Overall header."""
        self._batch_total = total
        self._update()

    def finish_row(
        self, key: str, *, ok: bool = True, message: str = "", pages: int = 0
    ) -> None:
        """Retire a row into the completed section of the live area."""
        st = self._rows.get(key)
        if st is None or st.status == "done":
            return
        st.status = "done"
        st.ok = ok
        st.result = message
        st.pages = pages or st.total
        st.finished_at = time.monotonic()
        self._progress.pop(key, None)
        self._update()

    def remove_row(self, key: str) -> None:
        self._progress.pop(key, None)
        self._rows.pop(key, None)
        self._logged_stages.pop(key, None)
        if key in self._order:
            self._order.remove(key)
        self._update()

    def set_status(self, key: str, text: str) -> None:
        st = self._rows.get(key)
        if st is None or st.stage == text:
            return
        st.stage = text
        st.last_tick = time.monotonic()
        self._update()

    def set_activity(self, key: str, text: str) -> None:
        """Update the transient activity text without logging a stage line."""
        st = self._rows.get(key)
        if st is None or st.stage == text:
            return
        st.stage = text
        st.last_tick = time.monotonic()
        self._update()

    def set_label(self, key: str, text: str) -> None:
        """Persist a per-row identity (e.g. ``Ep. 2  (2/3)``) shown next to the
        transient stage text in the row header."""
        st = self._rows.get(key)
        if st is None or st.label == text:
            return
        st.label = text
        st.last_tick = time.monotonic()
        self._update()

    def note_stage(self, key: str, text: str) -> bool:
        """True the first time a ``(key, text)`` stage transition is logged.

        Used by :class:`_RowSink` to emit ``-v`` lifecycle lines only once per
        distinct stage, instead of repeating the same text on every progress
        refresh.
        """
        if self._logged_stages.get(key) == text:
            return False
        self._logged_stages[key] = text
        return True

    def show_progress(self, key: str, total: int) -> None:
        if self._quiet or total <= 0:
            return
        st = self._rows.get(key)
        if st is None:
            st = RowState(key=key, total=total, started_at=time.monotonic())
            self._rows[key] = st
            self._order.append(key)
        st.total = total
        progress = make_download_progress(state_getter=lambda: st)
        self._progress[key] = progress
        progress.add_task("  ", total=total)
        self._update()

    def update_progress(self, key: str, done: int) -> None:
        st = self._rows.get(key)
        clamped = 0
        if st is not None:
            total = st.total
            clamped = int(min(done, total)) if total and total > 0 else 0
            st.done = clamped
            st.last_tick = time.monotonic()
        prog = self._progress.get(key)
        if prog is None or not prog.tasks:
            return
        task = prog.tasks[0]
        prog.update(task.id, completed=clamped)
        self._update()

    def add_bytes(self, key: str, n: int) -> None:
        st = self._rows.get(key)
        if st is None or n <= 0:
            return
        _apply_ewma(st, n, time.monotonic())

    def clear_progress(self, key: str) -> None:
        self._progress.pop(key, None)
        self._update()

    # ── rendering ──────────────────────────────────────────────

    def _row_renderable(self, key: str) -> Group:
        st = self._rows.get(key)
        if st is None:
            return Group(Text(""))
        return _running_row_renderable(st, self._progress.get(key), self._frame)

    def _overall_renderable(self) -> Text | None:
        if self._batch_total is None:
            return None
        states = list(self._rows.values())
        done = sum(1 for st in states if st.status == "done")
        running = sum(1 for st in states if st.status == "running")
        queued = max(0, self._batch_total - done - running)
        nbytes = sum(st.bytes for st in states)
        elapsed = time.monotonic() - self._started if self._started else 0.0

        frac_done, frac_total = done, self._batch_total
        if self._batch_total == 1 and done == 0 and running == 1:
            single = next(st for st in states if st.status == "running")
            if single.total and single.done:
                frac_done, frac_total = single.done, single.total
        pct = (frac_done / frac_total * 100) if frac_total else 0.0

        w = len(str(self._batch_total))
        parts: list[tuple[str, str]] = [
            ("  Overall: ", f"bold {BRAND}"),
            (f"{frac_done:>{w}d}/{frac_total}  ", "bold white"),
            (_mini_bar(frac_done, frac_total), "white"),
            (f"  {pct:3.0f}%", "white"),
        ]
        parts.append((f"  {glyphs().bullet}  ", MUTED))
        parts.append((f"{running:>{w}d} running", "white"))

        parts.append((f"  {glyphs().bullet}  ", MUTED))
        parts.append((f"{queued:>{w}d} queued", MUTED if queued else f"dim {MUTED}"))
        if nbytes >= _MIN_BYTES_DISPLAY:
            parts.append((f"  {glyphs().bullet}  ", MUTED))
            parts.append((format_bytes_fixed(nbytes), "white"))
            if elapsed >= _MIN_SPEED_ELAPSED:
                speed = nbytes / elapsed
                parts.append((f"  {glyphs().bullet}  ", MUTED))
                parts.append((format_speed_fixed(speed), BRAND))
                if frac_done > 0 and frac_total > frac_done:

                    eta = _batch_eta(
                        states, frac_done, frac_total, nbytes, speed
                    )
                    if eta is not None:
                        parts.append((f"  {glyphs().bullet}  ETA ", MUTED))
                        parts.append((format_remaining_fixed(eta), BRAND))
        return Text.assemble(*parts)

    def _completed_renderable(self) -> Group | None:
        done_rows = [st for st in self._rows.values() if st.status == "done"]
        if not done_rows:
            return None
        done_rows.sort(key=lambda st: st.finished_at)
        lines: list[Any] = []
        for st in done_rows:
            glyph = glyphs().success if st.ok else glyphs().err
            style = SUCCESS if st.ok else ERROR
            label = st.label or st.key
            line = Text.assemble(
                (f"  {glyph} ", f"bold {style}"),
                (
                    _truncate_label(label, max_width=int(err_console.width * 0.8)),
                    f"dim {MUTED}",
                ),
            )
            meta: list[str] = []
            if st.pages:
                meta.append(f"({st.pages}p)")
            if st.bytes >= _MIN_BYTES_DISPLAY:
                meta.append(format_bytes(st.bytes))
            if st.result:
                meta.append(st.result)
            if (
                st.finished_at
                and st.started_at
                and st.finished_at - st.started_at >= 1.0
            ):
                meta.append(_format_duration(st.finished_at - st.started_at))
            if meta:
                line.append(f"  {glyphs().bullet}  ", style=MUTED)
                line.append(f"  {glyphs().bullet}  ".join(meta), style=MUTED)
            lines.append(line)
        return Group(*lines)

    def _render(self) -> Group:
        parts: list[Any] = []
        overall = self._overall_renderable()
        if overall is not None:
            parts.append(overall)
            parts.append(Text(""))
        for key in self._order:
            st = self._rows.get(key)
            if st is not None and st.status == "running":
                parts.append(self._row_renderable(key))
        completed = self._completed_renderable()
        if completed is not None:
            parts.append(Text(""))
            parts.append(completed)
        return Group(*parts)

    def _needs_frame(self) -> bool:
        """True when the frame owner must rebuild: state changed, or a running
        row's spinner glyph must keep animating."""
        if self._dirty:
            return True
        return any(st.status == "running" for st in self._rows.values())

    def _update(self) -> None:

        if self._paused or self._live is None or self._done:
            return
        self._dirty = True

    def refresh_now(self) -> None:
        """Force a synchronous frame rebuild, bypassing the spin tick.

        Used right before a durable result line is printed so the retiring
        row's final state (and the Overall fraction it just moved) is what is
        on screen when scrollback lands — not a frame up to 80ms stale.
        """
        if self._paused or self._live is None or self._done:
            return
        with contextlib.suppress(Exception):
            self._live.update(self._render(), refresh=True)
        self._dirty = False

    async def _spin(self) -> None:
        while not self._done:
            if not self._paused and self._live is not None and self._needs_frame():
                try:
                    self._live.update(self._render(), refresh=True)
                except Exception:
                    # One bad render must never freeze the spinner while rows
                    # keep downloading underneath it.
                    vlog(VERBOSE, "activity render failed; keeping last frame")
                self._dirty = False
            self._frame += 1
            await asyncio.sleep(SPIN_INTERVAL)

    async def succeed(self, message: str) -> None:
        await self._close_with(print_success, message)

    async def fail(self, message: str) -> None:
        await self._close_with(print_error, message)

    async def _close_with(self, printer, message: str) -> None:
        self._done = True
        if self._spin_task is not None:
            self._spin_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._spin_task
            self._spin_task = None
        if self._live is not None:
            self._live.stop()
        unregister_active()
        printer(message)


def print_chapter_preview(
    chapters: list[dict],
    total: int,
    series_title: str,
    have_urls: set[str] | None = None,
) -> None:
    """Preview a series' chapter list.

    When ``have_urls`` is given, chapters already present are marked ✓ and
    new ones ↓; when everything is already downloaded an "up to date" line
    replaces the table. Without ``have_urls`` the output matches the legacy
    table exactly.
    """
    if not chapters:
        return
    marks_enabled = have_urls is not None
    have_urls = have_urls if have_urls is not None else set()

    n = len(chapters)
    have_count = 0
    if marks_enabled:
        have_count = sum(
            1 for c in chapters
            if normalize_url_key(c.get("url") or "") in have_urls
        )
    new_count = n - have_count

    _active_console().print()
    chapters_word = "chapter" if total == 1 else "chapters"
    header = (
        f"  [bold]Series:[/] [white]{esc(series_title)}[/]  "
        f"[{MUTED}]({total} {chapters_word})[/]"
    )
    if marks_enabled:
        if have_count:
            header += (
                f"  [bold {SUCCESS}]{have_count} downloaded[/] "
                f"[{MUTED}]{glyphs().bullet}[/]"
            )
        header += f"  [bold {INFO}]{new_count} new[/]"
    _active_console().print(header)

    if marks_enabled and new_count == 0:
        err_console.print(
            f"  [bold {SUCCESS}]{glyphs().success}[/] [bold]Series up to date.[/]"
        )
        _active_console().print()
        return

    table = Table(show_header=False, box=None, padding=(0, 2))
    if marks_enabled:
        table.add_column("", style=MUTED, width=4)
    table.add_column("#", style=MUTED, width=4)
    table.add_column("Title", style="white")

    shown = (
        list(range(n)) if n <= 6 else [0, 1, 2, n - 3, n - 2, n - 1]
    )
    prev = -1
    for i in shown:
        if prev >= 0 and i > prev + 1:
            row = (
                [glyphs().ellipsis, glyphs().ellipsis, glyphs().ellipsis]
                if marks_enabled
                else [glyphs().ellipsis, glyphs().ellipsis]
            )
            table.add_row(*row, style=MUTED)
        ep = chapters[i]
        label = esc(ep.get("title") or ep.get("episode_no", ""))
        if marks_enabled:
            mark = glyphs().ok if normalize_url_key(ep.get("url") or "") in have_urls else "new"
            mark_style = SUCCESS if mark == glyphs().ok else INFO
            table.add_row(f"[{mark_style}]{mark}[/]", str(i + 1), label)
        else:
            table.add_row(str(i + 1), label)
        prev = i
    _active_console().print(table)
    _active_console().print()


def print_failure_recap(failures: list[tuple[str, str]]) -> None:
    """Print the final list of failed downloads as one grouped, deduped unit.

    Failures are grouped by reason and each distinct reason is shown once with
    its labels indented beneath — N identical errors collapse into a single row
    with a count instead of N repeated lines.
    """
    if not failures:
        return
    _console = _active_console()
    _console.print(f"  [bold {ERROR}]{glyphs().err}[/] [bold]Failed:[/]")
    grouped: dict[str, list[str]] = {}
    for label, reason in failures:
        grouped.setdefault(reason, []).append(label)
    for reason, labels in grouped.items():
        count = f"  [{MUTED}]x{len(labels)}[/]" if len(labels) > 1 else ""
        _console.print(
            f"    [{ERROR}]{glyphs().bullet}[/] [white]{esc(reason)}[/]{count}"
        )
        for label in labels:
            _console.print(f"      [{MUTED}]{esc(label)}[/]")
    _console.print()


def print_partial_recap(partials: list[tuple[str, str]]) -> None:
    """Print the final list of incomplete downloads, grouped like failures.

    A partial chapter saved its archive but is missing pages; it is not a
    failure (a rerun resumes it), so it gets its own line distinct from the
    ``Failed:`` block.
    """
    if not partials:
        return
    _console = _active_console()
    _console.print(f"  [bold {WARNING}]{glyphs().warn}[/] [bold]Incomplete:[/]")
    grouped: dict[str, list[str]] = {}
    for label, reason in partials:
        grouped.setdefault(reason, []).append(label)
    for reason, labels in grouped.items():
        count = f"  [{MUTED}]x{len(labels)}[/]" if len(labels) > 1 else ""
        _console.print(
            f"    [{WARNING}]{glyphs().bullet}[/] [white]{esc(reason)}[/]{count}"
        )
        for label in labels:
            _console.print(f"      [{MUTED}]{esc(label)}[/]")
    _console.print()


def print_table(title: str | None, columns: list[str], rows: list[list]) -> None:
    """Render a simple table (no borders). Cells may contain rich markup.

    Tables are final results, so they go to stdout (``console``).
    """
    table = Table(title=title, show_header=True, box=None, padding=(0, 2))
    for col in columns:
        table.add_column(col, style="bold")
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)


def format_bytes(n: int) -> str:
    """Format a byte count as a human-readable size (e.g. ``2.3 MB``)."""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


_HELP_OPT_COL = 32


def _help_header(title: str) -> None:
    """Render a section header using the semantic token system."""
    console.print(Text(f"{title}:", style=style("info", bold=True)))


def _help_opt_row(
    flags: str,
    metavar: str,
    desc: str,
    *,
    default: str | None = None,
    choices: str | None = None,
) -> None:
    """Render one option row with consistent gutter, description, and metadata.

    The row structure is::

        <flags> <metavar>  <desc>    [default: X]  [possible values: …]

    The gutter adapts to the widest flag+metavar column on the screen.
    """
    t = Text("  ")
    t.append(flags, style=style("brand", bold=True))
    if metavar:
        t.append(" ")
        t.append(metavar, style=style("accent"))
    pad = _HELP_OPT_COL - len(t.plain)
    if pad > 0:
        t.append(" " * pad)
    else:
        t.append("  ")
    t.append(desc)
    meta_parts: list[str] = []
    if default:
        meta_parts.append(f"[default: {default}]")
    if choices:
        meta_parts.append(f"[possible values: {choices}]")
    if meta_parts:
        t.append("    " + "  ".join(meta_parts), style=style("muted"))
    console.print(t)


def _help_pointer(text: str) -> None:
    """Render a muted footer pointer line."""
    console.print(f"  [{style('muted')}]{text}[/]")


def _help_usage(lines: list[str]) -> None:
    """Render a Usage: block with multiple invocation shapes."""
    _help_header("Usage")
    for line in lines:
        console.print(f"  [bold]{esc(line)}[/]")
    console.print()


def _arg_metavar(action: argparse.Action) -> str:
    """Argparse-style placeholder for an action's value slot ("" if none)."""
    if action.nargs == 0:
        return ""
    metavar = action.metavar
    if metavar is None:
        if action.choices is not None:
            return "{" + ",".join(str(c) for c in action.choices) + "}"
        metavar = action.dest.upper()
    if isinstance(metavar, tuple):
        return " ".join(str(m) for m in metavar)
    return str(metavar)


def _arg_default(action: argparse.Action) -> str | None:
    """Default-value label for an action, or None if it has no meaningful one."""
    if action.nargs == 0:
        return None
    default = action.default
    if default is None or default is False:
        return None
    return str(default)


def _is_help_action(action: argparse.Action) -> bool:
    return bool(set(action.option_strings) & {"-h", "--help"})


def print_parser_help(parser: argparse.ArgumentParser) -> None:
    """Styled help for an argparse (sub)command parser.

    Mirrors the main :func:`print_help` look so every subcommand
    (list/info/latest/remove/restore/update/cookie/list-sources) renders
    consistently instead of raw argparse text.
    """
    console.print()

    _help_usage([f"{esc(parser.prog)} [OPTIONS]"])

    if parser.description:
        console.print(parser.description)
        console.print()

    positionals = [
        a
        for a in parser._actions
        if not a.option_strings
        and not isinstance(a, argparse._SubParsersAction)
        and a.help != argparse.SUPPRESS
    ]
    optionals = [a for a in parser._actions if a.option_strings]
    help_options = [a for a in optionals if _is_help_action(a)]
    optionals = [a for a in optionals if not _is_help_action(a)]
    subparsers = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]

    if positionals:
        _help_header("Arguments")
        for a in positionals:
            _help_opt_row(a.dest, _arg_metavar(a), a.help or "")
        console.print()

    _help_header("Options")
    for a in optionals:
        if a.help == argparse.SUPPRESS:
            continue
        flags = ", ".join(a.option_strings)
        choices_str = (
            ", ".join(str(c) for c in a.choices) if a.choices else None
        )
        _help_opt_row(flags, _arg_metavar(a), a.help or "",
                       default=_arg_default(a), choices=choices_str)
    for a in help_options:
        flags = ", ".join(a.option_strings)
        _help_opt_row(flags, "", a.help or "")
    console.print()

    for a in subparsers:
        if a.help == argparse.SUPPRESS:
            continue
        _help_header("Commands")
        for choice in a._choices_actions:
            _help_opt_row(choice.dest, "", choice.help or "")
        console.print()


def print_help() -> None:
    """Print the built-in help screen (full ``--help`` version)."""
    console.print()

    _help_usage([
        "comic-dl [OPTIONS]",
        "comic-dl [URL]",
        "comic-dl -u <URL> [OPTIONS]",
        "comic-dl -f <FILE> [OPTIONS]",
        "comic-dl help <COMMAND>",
    ])

    console.print("Download comic and manga galleries from supported sites and compile")
    console.print("them into CBZ, ZIP, and CBT archives.")
    console.print(
        f"  [{style('muted')}]Run without options to enter interactive mode (requires a TTY).[/]"
    )
    console.print()

    # ── Commands (purpose-grouped) ──────────────────────────────
    _help_header("Download")
    _help_opt_row("-u, --url", "<URL>", "Download a single gallery URL")
    _help_opt_row("-f, --file", "<FILE>", "Download URLs from a text file (errors cite file:line)")
    _help_opt_row("update", "<SERIES|all>", "Download newly-released chapters for tracked series")
    console.print()

    _help_header("Library")
    console.print(
        f"  [{style('muted')}](-o selects the library root; series by title, ID, or URL)[/]"
    )
    _help_opt_row("list", "", "List series in the library", choices=None)
    _help_opt_row("info", "<SERIES>", "Show details for one series")
    _help_opt_row("latest", "[-n N]", "Chapters downloaded in the last N days", default="7")
    _help_opt_row("remove", "<SERIES>", "Move a series to trash and forget it")
    _help_opt_row("restore", "<SERIES>", "Bring a trashed series back")
    console.print(
        f"  [{style('muted')}]Aliases: --list, --info, --latest, --remove, --restore  "
        f"({glyphs().dash} list/info/latest accept --json; remove/restore accept --dry-run)[/]"
    )
    console.print()

    _help_header("Manage")
    _help_opt_row("cookie", "ls|set|clear [HOST]", "Manage the persistent cookie jar")
    _help_opt_row("cache", "clear|status", "Inspect or clear the scrape response cache")
    _help_opt_row("config", "path|show|init", "Locate, inspect, or create config.toml")
    console.print()

    _help_header("Inspect & integrate")
    _help_opt_row(
        "--list-sources", "[--json] [--plugin] [QUERY]",
        "Search supported sites (TTY: interactive)",
    )
    _help_opt_row("completion", "bash|zsh|fish", "Print a shell completion script")
    _help_opt_row("--version", "", "Show version")
    _help_opt_row("-h, --help, -?", "", "Show this help message")
    console.print()

    # ── Options (category-grouped) ──────────────────────────────
    _help_header("Layout & output")
    _help_opt_row("-o, --output", "<DIR>", "Output directory", default="~/Downloads/comic-dl")
    _help_opt_row("--force", "", "Overwrite existing CBZ files")
    _help_opt_row(
        "--dry-run", "",
        "Resolve each URL and preview what would download/skip/redownload",
    )
    _help_opt_row("--json", "", "Emit machine-readable JSON on stdout; disables prompts")
    console.print(
        f"  [{style('muted')}]--force with --file and multiple URLs asks before re-downloading[/]"
    )
    console.print()

    _help_header("Download tuning")
    _help_opt_row("-c, --concurrency", "<N>", "Parallel page downloads per chapter", default="5")
    _help_opt_row("--parallel", "<N>", "Max URLs in flight across a batch (1-16)", default="5")
    _help_opt_row(
        "--chapter-parallel", "<N>",
        "Max chapters of a series downloading at once (1-8)", default="1",
    )
    _help_opt_row(
        "--chapters", "<SPEC>",
        "Chapters to download in a series (all, or 1-3,5 ranges)",
    )
    _help_opt_row(
        "--compress", "<MODE>",
        "CBZ compression: stored (default), deflate, deflate:0-9",
        default="stored",
    )
    _help_opt_row(
        "--format", "<FMT>",
        "Archive format", default="cbz",
        choices="cbz, zip, cbt",
    )
    _help_opt_row("--max-image-size", "<SIZE>", "Maximum size per image", default="100MB")
    _help_opt_row("--max-size", "<SIZE>", "Maximum total download size (0 = unlimited)")
    console.print(
        f"  [{style('muted')}]Size suffixes: 500MB, 2GB, 512KB; plain integers (bytes) also work[/]"
    )
    console.print()

    _help_header("HTTP & politeness")
    _help_opt_row(
        "--impersonate", "<PROFILE>",
        "TLS/HTTP impersonation profile", default="chrome146",
    )
    _help_opt_row(
        "--solver", "<MODE>",
        "Cloudflare challenge solver", default="auto",
        choices="auto, impersonation, webview, off",
    )
    _help_opt_row("--no-cookie", "", "Disable the persistent cookie jar for this run")
    _help_opt_row("--no-rate", "", "Disable per-site rate limiting for this run")
    _help_opt_row("--no-cache", "", "Disable the on-disk scrape response cache for this run")
    _help_opt_row(
        "--no-generic", "",
        "Disable the generic fallback scraper (unknown hosts report Unsupported URL)",
    )
    console.print()

    _help_header("Display")
    _help_opt_row("-q, --quiet", "", "Show errors only (mutually exclusive with -v)")
    _help_opt_row("-v, -vv, -vvv", "", "Increase diagnostic verbosity (see below)")
    _help_opt_row("--no-banner", "", "Suppress the ASCII brand banner (shown only on a terminal)")
    _help_opt_row(
        "--color", "<MODE>",
        "Control ANSI colors", default="auto",
        choices="auto, always, never",
    )
    _help_opt_row("--no-color", "", "Disable ANSI colors (alias for --color never)")
    _help_opt_row("--debug-file", "<PATH>", "Divert -vvv trace diagnostics to PATH")
    _help_opt_row("--config", "<PATH>", "Path to a custom config.toml")
    console.print()

    console.print("  [bold]Verbosity:[/]")
    console.print(f"    0 normal   {glyphs().dash} progress, status, warnings, errors, summary")
    console.print(
        f"    1 -v       {glyphs().dash} more context (source, paths, options, stats, stages)"
    )
    console.print(f"    2 -vv      {glyphs().dash} diagnostics (HTTP requests + timing, retries)")
    console.print(
        f"    3 -vvv     {glyphs().dash} full trace (response headers per request, tracebacks)"
    )
    console.print(
        f"    env        {glyphs().dash} COMIC_DL_TRACE_HTTP=1 always shows response headers"
    )
    console.print()

    # ── Footer ──────────────────────────────────────────────────
    _help_pointer("Run [bold]comic-dl help <command>[/] for help on a specific command.")
    _help_pointer("Full reference: https://github.com/fallen020/comic-dl/blob/main/docs/reference/cli.md")
    console.print()
    _help_header("Exit status")
    console.print("  0  success")
    console.print("  1  an error occurred (download failed, library error)")
    console.print("  2  usage error (bad arguments or configuration)")
    console.print("  130  interrupted (Ctrl-C / SIGINT)")


def print_help_summary() -> None:
    """Print the compact ``-h`` / ``-?`` help summary.

    One-line-per-option, no rationale blocks, no verbosity ladder, no
    exit-status block — just the essential scannable surface.
    """
    console.print()

    _help_usage([
        "comic-dl [OPTIONS]",
        "comic-dl [URL]",
        "comic-dl -u <URL> [OPTIONS]",
        "comic-dl -f <FILE> [OPTIONS]",
        "comic-dl help <COMMAND>",
    ])

    console.print("Download comic and manga galleries from supported sites and compile")
    console.print("them into CBZ, ZIP, and CBT archives.")
    console.print()

    # ── Commands (purpose-grouped, names only) ──────────────────
    _help_header("Download")
    _help_opt_row("-u, --url", "<URL>", "Download a single gallery URL")
    _help_opt_row("-f, --file", "<FILE>", "Download URLs from a text file")
    _help_opt_row("update", "<SERIES|all>", "Download new chapters for tracked series")
    console.print()

    _help_header("Library")
    _help_opt_row("list", "", "List series in the library")
    _help_opt_row("info", "<SERIES>", "Show details for one series")
    _help_opt_row("latest", "[-n N]", "Chapters downloaded in the last N days")
    _help_opt_row("remove", "<SERIES>", "Move a series to trash")
    _help_opt_row("restore", "<SERIES>", "Bring a trashed series back")
    console.print()

    _help_header("Manage")
    _help_opt_row("cookie", "ls|set|clear [HOST]", "Manage the persistent cookie jar")
    _help_opt_row("cache", "clear|status", "Inspect or clear the scrape response cache")
    _help_opt_row("config", "path|show|init", "Locate, inspect, or create config.toml")
    console.print()

    _help_header("Inspect & integrate")
    _help_opt_row("--list-sources", "", "Search supported sites")
    _help_opt_row("completion", "bash|zsh|fish", "Print a shell completion script")
    _help_opt_row("--version", "", "Show version")
    _help_opt_row("-h, --help, -?", "", "Show this help message")
    console.print()

    # ── Options (category-grouped, one line each) ───────────────
    _help_header("Layout & output")
    _help_opt_row("-o, --output", "<DIR>", "Output directory", default="~/Downloads/comic-dl")
    _help_opt_row("--force", "", "Overwrite existing CBZ files")
    _help_opt_row("--dry-run", "", "Preview what would download/skip/redownload")
    _help_opt_row("--json", "", "Machine-readable JSON on stdout")
    console.print()

    _help_header("Download tuning")
    _help_opt_row("-c, --concurrency", "<N>", "Parallel page downloads per chapter", default="5")
    _help_opt_row("--parallel", "<N>", "Max URLs in flight across a batch", default="5")
    _help_opt_row("--chapter-parallel", "<N>", "Max chapters downloading at once", default="1")
    _help_opt_row("--chapters", "<SPEC>", "Chapters to download (all, or 1-3,5)")
    _help_opt_row("--compress", "<MODE>", "CBZ compression", default="stored")
    _help_opt_row("--format", "<FMT>", "Archive format", default="cbz",
                   choices="cbz, zip, cbt")
    _help_opt_row("--max-image-size", "<SIZE>", "Maximum size per image", default="100MB")
    _help_opt_row("--max-size", "<SIZE>", "Maximum total download size")
    console.print()

    _help_header("HTTP & politeness")
    _help_opt_row(
        "--impersonate", "<PROFILE>",
        "TLS/HTTP impersonation profile", default="chrome146",
    )
    _help_opt_row("--solver", "<MODE>", "Cloudflare challenge solver", default="auto")
    _help_opt_row("--no-cookie", "", "Disable the persistent cookie jar")
    _help_opt_row("--no-rate", "", "Disable per-site rate limiting")
    _help_opt_row("--no-cache", "", "Disable the scrape response cache")
    _help_opt_row("--no-generic", "", "Disable the generic fallback scraper")
    console.print()

    _help_header("Display")
    _help_opt_row("-q, --quiet", "", "Show errors only")
    _help_opt_row("-v, -vv, -vvv", "", "Increase diagnostic verbosity")
    _help_opt_row("--no-banner", "", "Suppress the ASCII brand banner")
    _help_opt_row("--color", "<MODE>", "Control ANSI colors", default="auto")
    _help_opt_row("--no-color", "", "Disable ANSI colors")
    _help_opt_row("--debug-file", "<PATH>", "Divert trace diagnostics to PATH")
    _help_opt_row("--config", "<PATH>", "Path to a custom config.toml")
    console.print()

    _help_pointer("Run [bold]comic-dl help <command>[/] for help on a specific command.")

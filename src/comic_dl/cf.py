"""Cloudflare challenge detection and solver dispatch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from .antibot import looks_like_challenge
from .config import _RUNTIME_HTTP, http_setting
from .ui import TAG_WARNING, trace, vlog
from .utils import aclose_response, known_impersonate_profiles

_SOLVER_MODES = frozenset({"auto", "impersonation", "webview", "off"})

#: Value ``http_setting`` can never return from a real config layer, so its
#: presence means no layer set the key at all.
_UNSET: Any = object()

#: Older TLS profile for one automatic retry after a 403 on the default
#: profile. Firefox: some sites reject the newest Chromium signatures
#: outright, and firefox147 has a locked header pair in ``_STEALTH`` so the
#: wire headers agree with the negotiated fingerprint.
_FALLBACK_IMPERSONATE = "firefox147"


def fallback_impersonate(host: str | None = None) -> str | None:
    """Older impersonation profile for one automatic 403 retry, else ``None``.

    Only when the user kept the default profile: an explicit
    ``--impersonate``, ``[http] impersonate``, or per-host impersonate is
    never second-guessed. ``None`` when the pinned fallback is unknown to
    this curl_cffi build.
    """
    if host is not None:
        host = (host or "").lower() or None
    if http_setting("impersonate", _UNSET, host=host) is not _UNSET:
        return None
    if _FALLBACK_IMPERSONATE not in known_impersonate_profiles():
        return None
    return _FALLBACK_IMPERSONATE


def solver_mode(host: str | None = None) -> str:
    """Effective challenge solver for ``host``.

    Precedence: CLI ``--solver`` (via the runtime HTTP override) >
    ``[sources."<host>"] mode`` > ``[http] solver`` > ``off``.

    The runtime flag is read first explicitly because its key (``solver``)
    differs from the per-host key (``mode``), so a single ``http_setting``
    call cannot express the precedence.
    """
    for candidate in (
        _RUNTIME_HTTP.get("solver"),
        http_setting("mode", host=host),
        http_setting("solver", default="off"),
    ):
        if isinstance(candidate, str) and candidate in _SOLVER_MODES:
            return candidate
    return "off"


#: Hosts whose webview solve already failed this run.  Spawning the helper
#: once per host is enough: repeated 403s after a failed solve mean the
#: fingerprint-bound traffic can't fall back to plain HTTP anyway, and
#: re-spawning would pop a window per blocked request.
_failed_solves: set[str] = set()

#: Hosts whose stored-cookie replay was challenged already this run. A
#: replay can only be proven stale by trying it, but one failed probe per
#: run is enough — after that, fingerprint-bound scrapers go straight to
#: the webview session instead of burning a doomed plain request per call.
_replay_dead: set[str] = set()


def replay_dead(host: str) -> bool:
    """True when this run already saw ``host`` challenge a cookie replay."""
    return host.lower() in _replay_dead


def note_replay_dead(host: str) -> None:
    """Remember that ``host`` challenged a cookie replay (this run only)."""
    if host:
        _replay_dead.add(host.lower())


async def handle_challenge(url: str) -> bool:
    """Solve or clear the challenge for ``url``'s host using an escalation ladder.

    Resolution order (escalation):
    1. ``off`` — skip entirely, return False
    2. ``impersonation`` — try with fresh impersonation profile (fastest)
    3. reuse an already-running webview session for the host (no new window)
    4. ``webview`` — escalate to system webview if impersonation fails
    5. ``auto`` — try impersonation first, then webview if available

    Returns True when the caller should retry the request once with a fresh
    jar; False when retrying is pointless (solver disabled, or the solver
    failed and only impersonation remains for a hard challenge).

    Never raises: the worst case is a warning and no retry.
    """
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    # A 403 does not mean the clearance cookie is stale — fingerprint-bound
    # hosts (e.g. kagane.to) reject plain-HTTP replays even with valid
    # clearance. Deleting it here would throw away a working webview session.

    mode = solver_mode(host)
    trace(f"cf: handling challenge for {host} (mode={mode})")

    if mode == "off":
        trace(f"cf: solver disabled, not retrying {host}")
        return False

    if mode == "impersonation":
        return True

    try:
        # Import the solver lazily: `comic_dl.webview` must stay cheap to
        # import (it never imports pywebview at module scope). A static
        # import keeps the coroutine return type for the `await` below.
        from .webview import available as _webview_available
        from .webview import live_session_for, solve_challenge

        if live_session_for(url) is not None:
            # The session's WebKit context already holds clearance for this
            # host; opening a second helper would only pop another window.
            trace(f"cf: reusing live webview session for {host}, not re-solving")
            return True
        if host in _failed_solves:
            trace(f"cf: webview already failed for {host} this run, not re-spawning")
            return True
        if not _webview_available():
            trace(f"cf: webview solver unavailable for {host}")
        else:
            trace(f"cf: escalating to system webview for {host}")
            solved = await solve_challenge(url)
            if solved:
                trace(f"cf: webview harvested fresh cf_clearance for {host}")
                return True
            _failed_solves.add(host)
            vlog(
                1,
                f"webview solver failed for {host} — falling back to impersonation",
                tag=TAG_WARNING,
            )
    except ImportError:
        trace(f"cf: webview solver unavailable for {host}")
    except Exception as exc:
        trace(f"cf: webview solver error for {host}: {type(exc).__name__}")
    # Webview failed/absent: impersonation retry is the best we can do.
    return True


def _response_is_challenge(resp: Any) -> bool:
    status = getattr(resp, "status_code", None)
    headers = getattr(resp, "headers", None) or {}
    if not isinstance(status, int):
        return False
    if looks_like_challenge(status, headers, ""):
        return True
    text = getattr(resp, "text", None)
    return isinstance(text, str) and looks_like_challenge(status, headers, text)


async def retry_challenge_once(
    fetch: Callable[[], Awaitable[Any]],
    url: str,
    *,
    refetch: bool = True,
) -> Any:
    """Issue ``fetch()``; on a Cloudflare challenge, solve + retry once.

    Used by the scrape and download chokepoints so every chokepoint shares
    the exact same challenge semantics: detect, clear, solve, one retry.
    The result of the last attempt (retry or original) is always returned.

    ``refetch=False`` still runs the solver (clearance helps later
    requests) but returns the original response: non-idempotent methods
    are never replayed.
    """
    resp = await fetch()
    if not _response_is_challenge(resp):
        return resp
    trace(f"cf: challenge detected on {urlsplit(url).hostname}")
    if await handle_challenge(url):
        if not refetch:
            return resp
        await aclose_response(resp)
        return await fetch()
    return resp

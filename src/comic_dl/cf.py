"""Cloudflare challenge detection and solver dispatch."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from .antibot import BlockVerdict, looks_like_challenge
from .config import _RUNTIME_HTTP, http_setting
from .http import get_jar
from .ui import TAG_WARNING, trace, vlog


def solver_mode(host: str | None = None) -> str:
    """Effective challenge solver for ``host``.

    Precedence: CLI ``--solver`` (via the runtime HTTP override) >
    ``[sources."<host>"] mode`` > ``[http] solver`` > ``auto``.

    The runtime flag is read first explicitly because its key (``solver``)
    differs from the per-host key (``mode``), so a single ``http_setting``
    call cannot express the precedence.
    """
    cli = _RUNTIME_HTTP.get("solver")
    if isinstance(cli, str) and cli in {"auto", "impersonation", "webview", "off"}:
        return cli
    mode = http_setting("mode", host=host)
    if isinstance(mode, str) and mode in {"auto", "impersonation", "webview", "off"}:
        return mode
    global_mode = http_setting("solver", default="auto")
    if isinstance(global_mode, str) and global_mode in {
        "auto", "impersonation", "webview", "off",
    }:
        return global_mode
    return "auto"


async def handle_challenge(url: str, verdict: BlockVerdict | None = None) -> bool:
    """Solve or clear the challenge for ``url``'s host using an escalation ladder.

    Resolution order (escalation):
    1. ``off`` — skip entirely, return False
    2. ``impersonation`` — try with fresh impersonation profile (fastest)
    3. ``webview`` — escalate to system webview if impersonation fails
    4. ``auto`` — try impersonation first, then webview if available

    Returns True when the caller should retry the request once with a fresh
    jar; False when retrying is pointless (solver disabled, or the solver
    failed and only impersonation remains for a hard challenge).

    Never raises: the worst case is a warning and no retry.
    """
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    jar = get_jar()
    if jar is not None:
        jar.delete(host, "cf_clearance")

    mode = solver_mode(host)
    trace(
        f"cf: handling challenge for {host} "
        f"(vendor={verdict.vendor if verdict else 'unknown'}, "
        f"kind={verdict.kind if verdict else 'unknown'}, "
        f"mode={mode})"
    )

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
        from .webview import solve_challenge

        if not _webview_available():
            trace(f"cf: webview solver unavailable for {host}")
        else:
            trace(f"cf: escalating to system webview for {host}")
            solved = await solve_challenge(url)
            if solved:
                trace(f"cf: webview harvested fresh cf_clearance for {host}")
                return True
            vlog(
                1,
                f"webview solver failed for {host} — falling back to impersonation",
                tag=TAG_WARNING,
            )
    except (ImportError, ModuleNotFoundError):
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


async def _close_resp(resp: Any) -> None:
    """Best-effort close of a (possibly streaming) response before a retry."""
    closer = getattr(resp, "aclose", None) or getattr(resp, "close", None)
    if callable(closer):
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
        except Exception:  # nosec B110
            pass


async def retry_challenge_once(
    fetch: Callable[[], Awaitable[Any]],
    url: str,
) -> Any:
    """Issue ``fetch()``; on a Cloudflare challenge, solve + retry once.

    Used by the scrape and download chokepoints so every chokepoint shares
    the exact same challenge semantics: detect, clear, solve, one retry.
    The result of the last attempt (retry or original) is always returned.
    """
    resp = await fetch()
    if not _response_is_challenge(resp):
        return resp
    trace(f"cf: challenge detected on {urlsplit(url).hostname}")
    if await handle_challenge(url):
        await _close_resp(resp)
        return await fetch()
    return resp

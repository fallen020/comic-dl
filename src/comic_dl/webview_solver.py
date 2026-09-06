"""Standalone cross-platform webview challenge solver (subprocess helper).

The main ``comic_dl`` process runs in an async event loop; pywebview (the
system webview) owns its own GUI loop and must run on the main thread, so
the webview always runs here — a dedicated subprocess — and talks to the
parent over stdout as one JSON document.

Two modes:

* one-shot (default): open a *visible* native window (Windows WebView2 /
  macOS WKWebView / Linux WebKitGTK), load the target URL, wait for a
  ``cf_clearance`` cookie (Cloudflare runs its JS/Turnstile challenge inside
  the real engine; the user completes any interactive steps), then print
  every cookie for the site and exit.

* ``--serve``: after a ``cf_clearance`` lands, stay open and act as a
  long-lived *request session*. The parent writes JSON request lines to
  stdin (``{"id", "method", "url", "headers", "body"}``); each is executed
  as a same-origin synchronous ``XMLHttpRequest`` from inside the page, so
  it carries the session's cookies and WebKit TLS fingerprint — which a
  replayed cookie cannot. The JSON response line (``{"id", "status",
  "headers", "body_b64"}``) is written to stdout. ``{"shutdown": true}`` or
  EOF tears the window down.

The window is shown in both modes because Cloudflare fingerprints hidden/
offscreen renders and interactive challenges need the user.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import email.utils
import json
import os
import sys
import time
from typing import Any
from urllib.parse import urlsplit

from .webview_constants import (
    CLEARANCE_NAME,
    COOKIE_TIMEOUT,
    MAX_FRAME_BYTES,
    POLL_INTERVAL,
)
from .webview_constants import (
    origin_of as _origin_of,
)

_BRIDGE_READY_JS = "typeof pywebview !== 'undefined' && typeof pywebview.api !== 'undefined'"

# Headers that must not be set via XHR (browser-managed or security-sensitive).
_BLOCKED_HEADERS = frozenset({
    "content-length",
    "host",
    "connection",
    "cookie",
    "origin",
    "referer",
})


def _xhr_js(method: str, url: str, headers: dict, body: str | None) -> str:
    """JS that runs one same-origin synchronous XHR and returns a JSON string.

    The request runs from the loaded page's context, so it automatically
    carries the session's cookies and WebKit TLS fingerprint — the two things
    a replayed ``cf_clearance`` cannot reproduce. The response is returned as
    ``{status, headers, body_b64}`` (body base64 via TextEncoder so binary
    payloads survive).
    """
    header_lines = "\n".join(
        f"xhr.setRequestHeader({json.dumps(str(k))}, {json.dumps(str(v))});"
        for k, v in (headers or {}).items()
        if k.lower() not in _BLOCKED_HEADERS
    )
    body_literal = "null" if body is None else json.dumps(body)
    return (
        "(function () {\n"
        "try {\n"
        "var xhr = new XMLHttpRequest();\n"
        f"xhr.open({json.dumps(str(method))}, {json.dumps(url)}, false);\n"
        "xhr.withCredentials = true;\n"
        f"{header_lines}\n"
        f"xhr.send({body_literal});\n"
        "var raw = xhr.getAllResponseHeaders() || '';\n"
        "var headers = {};\n"
        "var lines = raw.split(/\\r?\\n/);\n"
        "for (var i = 0; i < lines.length; i++) {\n"
        "  var idx = lines[i].indexOf(':');\n"
        "  if (idx > 0) headers[lines[i].slice(0, idx).trim().toLowerCase()] = lines[i].slice(idx + 1).trim();\n"  # noqa: E501
        "}\n"
        "var b64 = '';\n"
        "try {\n"
        "  var bytes = new TextEncoder().encode(xhr.responseText);\n"
        "  var binary = '';\n"
        "  for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);\n"
        "  b64 = btoa(binary);\n"
        "} catch (e) { b64 = ''; }\n"
        f"return JSON.stringify({{status: xhr.status, headers: headers, body_b64: b64}});\n"
        "} catch (e) {\n"
        f"return JSON.stringify({{status: 0, headers: {{}}, body_b64: {json.dumps('')}, error: String(e && e.message || e)}});\n"  # noqa: E501
        "}\n"
        "})()"
    )


def _decode_body(body_b64: str | None) -> bytes:
    """Base64-decoded XHR response body (``""`` for empty)."""
    if not body_b64:
        return b""
    with contextlib.suppress(Exception):
        return base64.b64decode(body_b64)
    return b""


def _parse_expiry(morsel: Any) -> int | None:
    """Best-effort epoch expiry from a SimpleCookie morsel.

    ``max-age=0`` means the cookie expires immediately (returns 1, i.e. epoch
    start) rather than ``now + 0`` which would make it look valid for one
    more second.
    """
    expires_raw = morsel.get("expires")
    if expires_raw:
        try:
            parsed = email.utils.parsedate_to_datetime(expires_raw)
            if parsed is not None:
                return int(parsed.timestamp())
        except (TypeError, ValueError, OverflowError):
            pass
    max_age = morsel.get("max-age")
    if max_age is not None:
        try:
            age = int(max_age)
        except (TypeError, ValueError):
            pass
        else:
            if age <= 0:
                return 1  # already expired
            return int(time.time()) + age
    return None


def _extract_cookies(cookies: list[Any], host: str) -> list[dict[str, Any]]:
    """Flatten pywebview's SimpleCookie objects into plain dicts.

    Deduplicates by (name, domain, path) — first cookie wins.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for simple in cookies or []:
        for morsel in getattr(simple, "values", list)():
            name = getattr(morsel, "key", None)
            value = getattr(morsel, "value", None)
            if name is None or value is None:
                continue
            domain = (morsel.get("domain") or host).lstrip(".")
            path = morsel.get("path") or "/"
            key = (name, domain, path)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "name": str(name),
                    "value": str(value),
                    "domain": domain,
                    "path": path,
                    "expires": _parse_expiry(morsel),
                }
            )
    return out


def _result(ok: bool, cookies: list[dict[str, Any]]) -> None:
    print(json.dumps({"ok": ok, "cookies": cookies}))
    sys.stdout.flush()


def _handle_request(window: Any, req: dict[str, Any], page_origin: str) -> dict[str, Any] | None:
    """Execute one in-page XHR for a validated request dict.

    ``req`` is a parsed ``{id, method, url, headers, body}`` line; the result
    is the matching response dict ``{id, status, headers, body_b64}`` (with an
    ``error`` key when the in-page payload or the transport coughed up one),
    or ``None`` for a shutdown request. Never raises: failures are folded into
    a status-0 response so the parent always gets a well-formed line.
    """
    if req.get("shutdown"):
        return None
    try:
        method = str(req.get("method", "GET"))
        url = str(req.get("url", ""))
        headers = req.get("headers") or {}
        body = req.get("body")
        if not isinstance(body, (str, type(None))):
            body = json.dumps(body)

        # Validate that the request targets the same origin as the page.
        req_origin = _origin_of(url)
        if req_origin.lower() != page_origin.lower():
            return {
                "id": req.get("id"),
                "status": 0,
                "headers": {},
                "body_b64": "",
                "error": f"cross-origin request blocked: {req_origin} != {page_origin}",
            }

        # Filter unsafe headers.
        filtered_headers = {
            k: v for k, v in headers.items()
            if k.lower() not in _BLOCKED_HEADERS
        }

        js = _xhr_js(method, url, filtered_headers, body)
        raw = window.evaluate_js(js)
        payload = json.loads(raw) if isinstance(raw, str) else {}
        resp: dict[str, Any] = {
            "id": req.get("id"),
            "status": payload.get("status", 0),
            "headers": payload.get("headers", {}),
            "body_b64": payload.get("body_b64", ""),
        }
        if payload.get("error"):
            resp["error"] = payload["error"]
        return resp
    except Exception as exc:
        return {
            "id": req.get("id"),
            "status": 0,
            "headers": {},
            "body_b64": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _serve_loop(window: Any, page_origin: str) -> None:
    """Read JSON request lines from stdin, run each as an in-page XHR.

    Runs on the pywebview func thread (the GUI loop owns the main thread).
    Blocks on stdin until ``{"shutdown": true}`` or EOF; each request line is
    one ``{id, method, url, headers, body}`` and each response line is one
    ``{id, status, headers, body_b64}``.

    Lines exceeding ``MAX_FRAME_BYTES`` are rejected to protect against a
    misbehaving parent flooding stdin.
    """
    while True:
        line = _read_line(sys.stdin, MAX_FRAME_BYTES)
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(
                f"webview_solver: malformed JSON frame ({len(line)} bytes)",
                file=sys.stderr,
            )
            continue
        if not isinstance(req, dict):
            continue
        resp = _handle_request(window, req, page_origin)
        if resp is None:
            break
        print(json.dumps(resp))
        sys.stdout.flush()


def _read_line(stream: Any, max_bytes: int) -> str | None:
    """Read one line from *stream*, rejecting lines over *max_bytes*.

    Returns ``None`` on EOF.  Oversized lines are consumed (discarded) and
    ``None`` is returned to signal a protocol error.
    """
    line = stream.readline(max_bytes + 1)
    if not line:
        return None
    if len(line) > max_bytes:
        # Discard the rest of this oversized line.
        while True:
            chunk = stream.readline(1)
            if not chunk or chunk.endswith(b"\n"):
                break
        return None
    return line


def _wait_clearance(window: Any, host: str, timeout: float) -> bool:
    """Wait until the Cloudflare challenge is actually cleared.

    A ``cf_clearance`` cookie alone is not enough: with a persistent profile
    an expired/stale copy can be present while the page is still sitting on
    the "Just a moment..." interstitial (which also carries an active
    ``cf_chl_rc_ni`` cookie). Require both a fresh-enough cookie AND that the
    interstitial has been replaced by the real page.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = window.get_cookies()
        except Exception:  # page may not be ready yet
            raw = []
        cookies = _extract_cookies(raw, host)
        has_clearance = any(c["name"] == CLEARANCE_NAME for c in cookies)
        still_challenged = any(c["name"] == "cf_chl_rc_ni" for c in cookies)
        if has_clearance and not still_challenged:
            title = ""
            with contextlib.suppress(Exception):
                title = str(window.evaluate_js("document.title") or "")
            if "just a moment" not in title.lower():
                return True
        time.sleep(POLL_INTERVAL)
    return False


def _wait_bridge(window: Any, timeout: float) -> bool:
    """Wait for pywebview's JS bridge to be reachable inside the page."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(Exception):
            if window.evaluate_js(_BRIDGE_READY_JS):
                return True
        time.sleep(POLL_INTERVAL)
    return False


def main() -> int:
    """Entry point for the standalone solver subprocess."""
    parser = argparse.ArgumentParser(
        description="Open a system webview, pass the Cloudflare challenge, "
        "print the harvested cookies as JSON (or serve in-page requests)."
    )
    parser.add_argument("--url", required=True, help="URL to load in the webview")
    parser.add_argument(
        "--user-agent", default=None, help="User-Agent to present (matches curl_cffi)"
    )
    parser.add_argument("--timeout", type=float, default=COOKIE_TIMEOUT)
    parser.add_argument(
        "--solver", default=None, help="pywebview GUI override (gtk/qt/etc.)"
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="stay open and execute in-page XHR requests from stdin",
    )
    args = parser.parse_args()

    try:
        import webview
    except Exception as exc:  # pragma: no cover - backend probe path
        _result(False, [])
        print(f"webview_solver: cannot import pywebview: {exc}", file=sys.stderr)
        return 1

    s = urlsplit(args.url)
    host = (s.hostname or "").lower()
    page_origin = _origin_of(args.url)

    # create_window always returns a Window instance; None would mean a
    # fatal GUI-backend error that surfaces as an exception.
    window = webview.create_window(
        "comic-dl — solve Cloudflare challenge", args.url
    )
    if window is None:  # pragma: no cover - pywebview always returns a Window
        _result(False, [])
        print("webview_solver: pywebview returned no window", file=sys.stderr)
        return 1
    state: dict[str, Any] = {"cookies": []}

    def _poll() -> None:
        if _wait_clearance(window, host, args.timeout):
            try:
                raw = window.get_cookies()
            except Exception:
                raw = []
            state["cookies"] = _extract_cookies(raw, host)
        with contextlib.suppress(Exception):
            window.destroy()

    def _serve() -> None:
        got = _wait_clearance(window, host, args.timeout)
        if not got or not _wait_bridge(window, 30.0):
            print(json.dumps({"ready": False, "cookies": []}))
            sys.stdout.flush()
            with contextlib.suppress(Exception):
                window.destroy()
            return
        try:
            raw = window.get_cookies()
        except Exception:
            raw = []
        state["cookies"] = _extract_cookies(raw, host)
        print(json.dumps({"ready": True, "cookies": state["cookies"]}))
        sys.stdout.flush()
        _serve_loop(window, page_origin)
        with contextlib.suppress(Exception):
            window.destroy()

    try:
        if args.serve:
            # private_mode=False: the session needs cookie persistence across
            # requests so the cf_clearance stays valid for the session lifetime.
            webview.start(
                _serve,
                user_agent=args.user_agent,
                gui=args.solver,
                private_mode=False,
            )
            return 0
        webview.start(
            _poll,
            user_agent=args.user_agent,
            gui=args.solver,
            private_mode=False,
        )
    except Exception as exc:  # pragma: no cover - backend errors surface here
        print(f"webview_solver: webview backend failed: {exc}", file=sys.stderr)
        if args.serve:
            print(json.dumps({"ready": False, "cookies": []}))
        else:
            _result(False, [])
        return 1

    cookies = state.get("cookies", [])
    ok = any(c["name"] == CLEARANCE_NAME for c in cookies)
    _result(ok, cookies)
    # os._exit: some GTK/WebKit builds linger in their main loop after the
    # last window is destroyed.  A normal sys.exit would hang, so we force
    # an immediate exit after flushing the result.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())

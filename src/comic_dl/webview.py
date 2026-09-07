"""System-webview Cloudflare challenge solver (cross-platform).

The solver runs in a separate process so the webview's GUI loop never
collides with the async download pipeline (pywebview must own the main
thread). The standalone helper (:mod:`comic_dl.webview_solver`) opens a
*visible* native webview — WebView2 on Windows, WKWebView on macOS,
WebKitGTK on Linux — passes the Cloudflare challenge, and prints the
harvested cookies as JSON on stdout.

The browser is strictly a one-time interactive cookie acquisition mechanism:
``solve_challenge`` is only invoked when a real challenge is detected
(:func:`comic_dl.cf.handle_challenge`), and the harvested ``cf_clearance``
(valid ~1-2 hours) is reused by plain ``curl_cffi`` requests until the
server challenges again.

Some sites (e.g. kagane.to) bind ``cf_clearance`` to the exact TLS
fingerprint that minted it, so a WebKit-acquired cookie is 403-rejected when
replayed by curl_cffi regardless of User-Agent. For those, :class:
`WebViewSession` keeps the webview open and serves the request path itself:
each API request is executed as a same-origin XHR inside the page, which
carries the session's cookies *and* TLS fingerprint. Use ``session_request``
from a scraper when plain-HTTP replay is known to fail.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from .http import get_jar
from .ui import TAG_WARNING, print_dim, trace, vlog
from .utils import HTTP_CLIENT_ARGS, validate_request_url
from .webview_constants import (
    MAX_FRAME_BYTES,
    REQUEST_TIMEOUT,
    SESSION_TIMEOUT,
    SOLVE_TIMEOUT,
    STDERR_DRAIN_BYTES,
    WEBVIEW_IDLE_TIMEOUT,
    WEBVIEW_STREAM_CHUNK_BYTES,
)
from .webview_constants import (
    origin_of as _origin_of,
)

_HELPER_MODULE = "comic_dl.webview_solver"

_GI_IMPORT = "import gi; gi.require_version('WebKit2', '4.1')"


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def available() -> bool:
    """Whether a webview solver can plausibly run in this environment.

    Checks, in order: pywebview is importable, the GI bindings are present
    on at least one interpreter the helper could run under (Linux only), and
    a display or ``xvfb-run`` is reachable.  The GI probe walks the same
    candidate list as :func:`_helper_command`, so a system-wide PyGObject/
    WebKit install is honored even when the venv interpreter itself lacks
    ``gi`` — mirroring the interpreter the helper will actually spawn.  The
    authoritative check still happens when the subprocess actually starts —
    a missing GTK/WebKit system library makes the helper exit nonzero and
    :func:`solve_challenge` falls back to impersonation.

    .. note::
       Under ``xvfb-run`` the browser runs in a virtual framebuffer and is
       *not* visible to the user.  This is acceptable for challenge solving
       because Cloudflare's JS challenge does not require user interaction
       in most cases; interactive CAPTCHAs will still fail under Xvfb.
    """
    if importlib.util.find_spec("webview") is None:
        return False
    if os.name == "nt":
        return True
    # On Linux, probe for gi (PyGObject) before checking the display — the
    # helper can fail for reasons other than a missing display, and a missing
    # gi is the most common reason on headless systems.
    if sys.platform.startswith("linux"):
        if not any(
            _interpreter_has_gi(python)
            for python in _interpreter_candidates()
        ):
            trace(
                "webview: PyGObject/WebKit not available; "
                "install system packages for the webview solver:\n"
                "  Ubuntu/Debian: sudo apt install python3-gi python3-gi-cairo "
                "gir1.2-webkit2-4.1 gir1.2-gtk-4.0\n"
                "  Fedora: sudo dnf install python3-gobject webkit2gtk4.1\n"
                "  Arch: sudo pacman -S python-gobject webkit2gtk\n"
                "  Or use --solver impersonation to skip the webview."
            )
            return False
    if _has_display():
        return True
    if shutil.which("xvfb-run") is not None:
        return True
    if sys.platform.startswith("linux"):
        trace(
            "webview: no display available; "
            "use --solver impersonation or run under xvfb-run."
        )
    return False


def _helper_env() -> dict[str, str]:
    """Environment for the helper subprocess.

    On Linux, merge the current interpreter's ``purelib`` into ``PYTHONPATH``
    so pywebview (venv) is importable even when the helper runs under the
    system ``python3`` (which provides ``gi``). Also add the package source
    root — an editable install's ``.pth`` redirect is only honored by the
    venv interpreter, so the system interpreter needs the explicit path.
    """
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", "")
    with contextlib.suppress(Exception):
        from sysconfig import get_paths

        purelib = get_paths().get("purelib")
        if purelib and purelib not in env["PYTHONPATH"]:
            env["PYTHONPATH"] = (
                f"{purelib}{os.pathsep}{env['PYTHONPATH']}"
                if env["PYTHONPATH"]
                else purelib
            )
    with contextlib.suppress(Exception):
        package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if package_dir not in env["PYTHONPATH"]:
            env["PYTHONPATH"] = (
                f"{package_dir}{os.pathsep}{env['PYTHONPATH']}"
                if env["PYTHONPATH"]
                else package_dir
            )
    return env


def _interpreter_candidates() -> list[str]:
    """Python interpreters that might run the webview helper.

    Shared by :func:`available` and :func:`_helper_command` so the
    availability gate and the spawned helper agree on which interpreter can
    actually provide PyGObject/WebKit.  Tries the current interpreter first
    (covers a system install, or a venv created with
    ``--system-site-packages``), then the real system ``python3`` — not the
    venv's shim, which ``uv run``/virtualenvs put first on ``PATH`` and
    which usually lacks ``gi``.
    """
    candidates = [sys.executable]
    for path in (
        "/usr/bin/python3",
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3",
    ):
        if path not in candidates and os.path.exists(path):
            candidates.append(path)
    for other in _python_on_path():
        if other not in candidates:
            candidates.append(other)
    return candidates


def _interpreter_has_gi(python: str) -> bool:
    """Whether ``python`` can import PyGObject and require WebKit2 4.1."""
    probe = subprocess.run(  # nosec B603
        [python, "-c", _GI_IMPORT],
        capture_output=True,
        timeout=15,
    )
    return probe.returncode == 0


def _helper_command() -> list[str] | None:
    """argv for the helper subprocess; ``None`` when no usable interpreter."""
    module = [_HELPER_MODULE]
    if os.name == "nt":
        return [sys.executable, "-m", *module]
    for python in _interpreter_candidates():
        if _interpreter_has_gi(python):
            return [python, "-m", *module]
    return None


def _python_on_path() -> list[str]:
    """``python3``/``python`` on PATH that are not the current venv's shim."""
    out: list[str] = []
    bindir = os.path.dirname(sys.executable)
    for name in ("python3", "python"):
        for found in _iter_which(name):
            if found and found != sys.executable and os.path.dirname(found) != bindir:
                out.append(found)
    return out


def _iter_which(name: str) -> list[str]:
    found = shutil.which(name)
    return [found] if found else []


async def _stderr_drainer(proc: asyncio.subprocess.Process) -> None:
    """Continuously drain the helper's stderr so the pipe never fills up.

    The helper's stderr is captured for diagnostics, but a noisy WebKit
    process can fill the OS pipe buffer and block the child.  This task
    keeps reading chunk-by-chunk until EOF (process exit); ``read(-1)``
    cannot be used here because it blocks until the pipe closes.
    """
    if proc.stderr is None:
        return
    tail = bytearray()
    while True:
        try:
            chunk = await proc.stderr.read(4096)
        except Exception:
            break
        if not chunk:
            break
        tail.extend(chunk)
        if len(tail) > STDERR_DRAIN_BYTES:
            del tail[: len(tail) - STDERR_DRAIN_BYTES]
    if tail:
        trace(f"webview: helper stderr: {bytes(tail).decode(errors='ignore')}")


async def _run_helper(url: str, timeout: float) -> dict[str, Any]:
    """Spawn the helper, wait for cookies, return its JSON result (or empty).

    The helper has its own internal timeout (``--timeout``); the parent
    waits slightly longer (helper timeout + 10s grace) to account for
    process startup/shutdown overhead without racing the helper's timer.
    """
    cmd = _helper_command()
    if cmd is None:
        return {}
    # --user-agent is passed so the helper's webview presents the same UA as
    # curl_cffi — important for sites that bind cf_clearance to the UA.
    args = [
        *cmd,
        "--url",
        url,
        "--user-agent",
        HTTP_CLIENT_ARGS["headers"]["User-Agent"],
        "--timeout",
        str(timeout),
    ]
    if os.name != "nt" and not _has_display() and shutil.which("xvfb-run"):
        args = ["xvfb-run", "-a", *args]
    trace(f"webview: launching system webview for {urlsplit(url).hostname}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_helper_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        trace(f"webview: could not start solver: {type(exc).__name__}")
        return {}
    try:
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + 10
        )
    except TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        await proc.wait()
        trace("webview: solver timed out")
        return {}
    if _stderr:
        trace(f"webview: solver stderr: {_stderr.decode(errors='ignore')[:200]}")
    if proc.returncode not in (0, 1):
        trace(f"webview: solver exited with code {proc.returncode}")
        return {}
    try:
        data = json.loads(stdout.decode(errors="ignore"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _harvest(cookies: list[dict[str, Any]]) -> bool:
    """Store harvested cookies into the jar; True when cf_clearance landed."""
    jar = get_jar()
    found = False
    for c in cookies or []:
        name = c.get("name")
        value = c.get("value")
        host = (c.get("domain") or "").lstrip(".")
        if not name or value is None or not host:
            continue
        if name == "cf_clearance":
            found = True
        expires = c.get("expires")
        if not isinstance(expires, int) or expires <= 0:
            expires = None
        if jar is not None:
            jar.set(host, name, value, path=c.get("path") or "/", expires=expires)
    return found


async def solve_challenge(url: str) -> bool:
    """Solve the Cloudflare challenge for ``url`` in a visible system webview.

    Returns True when a fresh ``cf_clearance`` was harvested into the jar.
    Never raises: any failure returns False so the caller falls back to the
    impersonation path.
    """
    host = (urlsplit(url).hostname or "").lower()
    if not available():
        vlog(1, "webview solver unavailable — using impersonation only", tag=TAG_WARNING)
        return False
    print_dim(
        f"Opening a browser window to pass the {host} challenge — "
        "it closes by itself once cleared."
    )
    data = await _run_helper(url, SOLVE_TIMEOUT)
    cookies = data.get("cookies")
    ok = _harvest(cookies if isinstance(cookies, list) else [])
    if ok:
        trace(f"webview: fresh cf_clearance for {host}")
    else:
        trace(f"webview: no cf_clearance within {SOLVE_TIMEOUT:.0f}s for {host}")
    return ok


class SessionUnavailableError(RuntimeError):
    """Raised when a webview request session is needed but cannot start."""


class SessionRequestError(RuntimeError):
    """Raised when a request through the webview session fails."""


class SessionTransportError(SessionRequestError):
    """Raised when the session *pipe* fails during a stream.

    Distinct from :class:`SessionRequestError` (which covers in-page/HTTP
    outcomes) so the downloader can discard the session on a dead/truncated
    transport while keeping it alive for a plain HTTP 403 from inside the page.
    """


class _SessionStream:
    """One streamed response over a :class:`WebViewSession`'s pipe.

    Implements the downloader's ``_StreamResponse`` protocol (``status_code``,
    ``headers``, ``raise_for_status``, ``aiter_content``, ``aclose``). The
    header line is consumed before the object is returned, so ``status_code``
    is already populated; the body then arrives in ``WEBVIEW_STREAM_CHUNK_BYTES``
    chunks from the session's reader task. A premature EOF raises
    :class:`SessionTransportError` instead of yielding a truncated image.
    """

    def __init__(self, session: WebViewSession, timeout: float) -> None:
        self._session = session
        self._timeout = timeout
        self._chunks: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._header_error: BaseException | None = None
        self._header_stored = False
        self._closed = False
        self.status_code = 0
        self.headers: Mapping[str, str] = {}

    def _store_header(
        self, status: int, headers: dict[str, str]
    ) -> None:
        self.status_code = status
        self.headers = headers
        self._header_stored = True
        self._ready.set()

    def _fail(self, exc: BaseException) -> None:
        if self._header_stored:
            self._chunks.put_nowait(exc)
        else:
            self._header_error = exc
        self._ready.set()

    async def _await_headers(self) -> None:
        await self._ready.wait()
        if self._header_error is not None:
            raise self._header_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            from curl_cffi.requests.exceptions import HTTPError

            raise HTTPError(f"HTTP Error {self.status_code}", response=self)

    async def aiter_content(
        self, chunk_size: int | None = None
    ) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._chunks.get()
            if chunk is None:
                return
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        reader = self._session._stream_task
        if reader is not None and not reader.done():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader


class WebViewSession:
    """A long-lived webview subprocess that serves in-page HTTP requests.

    ``start`` spawns ``webview_solver --serve``, which solves the challenge
    for ``base_url`` and then reads JSON request lines from stdin, running
    each as a same-origin synchronous XHR. ``request`` writes one request line
    and reads the matching response line. The helper process owns the GUI, so
    this class is safe to use from the async download pipeline.

    ``base_url`` must include scheme and port (e.g. ``https://example.com:8443``)
    so the session correctly identifies its own origin.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._origin = _origin_of(base_url)
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._active_request: asyncio.Task[bytes] | None = None
        self._idle_timeout = WEBVIEW_IDLE_TIMEOUT
        self._idle_task: asyncio.Task[None] | None = None
        self._stream_task: asyncio.Task[None] | None = None

    @property
    def host(self) -> str:
        return (urlsplit(self._base_url).hostname or "").lower()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> bool:
        """Spawn the helper and wait for the ready handshake."""
        cmd = _helper_command()
        if cmd is None:
            return False

        args = [
            *cmd,
            "--serve",
            "--url",
            self._base_url,
            # --user-agent is intentionally omitted here.  The clearance is
            # bound to the webview's own User-Agent + TLS fingerprint;
            # presenting curl_cffi's Chrome UA makes Cloudflare reject every
            # in-page request (403).
            "--timeout",
            str(SESSION_TIMEOUT),
        ]
        if os.name != "nt" and not _has_display() and shutil.which("xvfb-run"):
            args = ["xvfb-run", "-a", *args]
        trace(f"webview: starting request session for {self._origin}")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_helper_env(),
                limit=MAX_FRAME_BYTES,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            trace(f"webview: session could not start: {type(exc).__name__}")
            return False
        if self._proc.stdout is None:
            await self.close()
            return False
        try:
            line = await asyncio.wait_for(
                self._proc.stdout.readline(), timeout=SESSION_TIMEOUT
            )
        except TimeoutError:
            await self.close()
            return False
        try:
            data = json.loads(line.decode(errors="ignore"))
        except json.JSONDecodeError:
            await self.close()
            return False
        if not isinstance(data, dict) or not data.get("ready"):
            await self.close()
            return False
        # Harvest cookies from the ready handshake into the jar so they're
        # available to plain-HTTP requests even before the first in-page call.
        ready_cookies = data.get("cookies")
        if isinstance(ready_cookies, list):
            _harvest(ready_cookies)
        # Keep draining the helper's stderr for the session lifetime so the
        # pipe buffer can't fill up and block the child.
        self._stderr_task = asyncio.create_task(_stderr_drainer(self._proc))
        self._arm_idle()
        return True

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: str | None = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> tuple[int, dict[str, str], bytes]:
        """Execute ``method`` ``url`` inside the page; ``(status, headers, body)``.

        Raises :class:`SessionRequestError` when the helper dies, the request
        errors out in-page, or no response arrives within ``timeout`` seconds.
        Only same-origin URLs are accepted (the page's origin must match the
        request target so cookies and fingerprint line up).
        """
        # Validate the URL structurally before checking the origin.
        validate_request_url(url)

        req_origin = _origin_of(url)
        if req_origin.lower() != self._origin.lower():
            raise SessionRequestError(
                f"webview session for {self._origin} cannot request {req_origin}"
            )

        async with self._lock:
            # An armed idle timer must not fire while a request is in flight;
            # cancel it now and re-arm once the request completes.
            self._cancel_idle()
            try:
                # Re-check liveness under the lock: a concurrent close() between
                # the early check and the pipe writes would leave proc None here.
                if self._proc is None or self._proc.returncode is not None:
                    raise SessionRequestError("webview session is not running")
                proc = self._proc
                if proc.stdin is None or proc.stdout is None:
                    raise SessionRequestError("webview session pipes are closed")

                self._next_id += 1
                req_id = self._next_id
                request = {
                    "id": req_id,
                    "method": method,
                    "url": url,
                    "headers": headers or {},
                    "body": body,
                }
                try:
                    proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError) as exc:
                    raise SessionRequestError(
                        "webview session pipe closed"
                    ) from exc
                # Expose the read as a cancellable task so close() can interrupt
                # an in-flight request; readline() raises ValueError for frames
                # over the pipe limit (MAX_FRAME_BYTES), which we map to an error.
                read_task = asyncio.create_task(proc.stdout.readline())
                self._active_request = read_task
                try:
                    try:
                        line = await asyncio.wait_for(read_task, timeout=timeout)
                    except TimeoutError as exc:
                        raise SessionRequestError(
                            f"webview session timed out after {timeout:.0f}s"
                        ) from exc
                    except ValueError as exc:
                        # An over-limit frame leaves the reader's buffer in an
                        # undefined state, so the session cannot be reused.
                        with contextlib.suppress(Exception):
                            await self.close()
                        raise SessionRequestError("response frame too large") from exc
                finally:
                    if self._active_request is read_task:
                        self._active_request = None
                if not line:
                    raise SessionRequestError("webview session closed early")
                try:
                    data = json.loads(line.decode(errors="ignore"))
                except json.JSONDecodeError as exc:
                    raise SessionRequestError(
                        "malformed response from webview session"
                    ) from exc
                if not isinstance(data, dict) or data.get("id") != req_id:
                    raise SessionRequestError("unexpected response id from webview session")
                if data.get("error"):
                    raise SessionRequestError(str(data["error"]))
                status = data.get("status", 0)
                if not isinstance(status, int):
                    status = 0
                headers_out = {
                    str(k): str(v) for k, v in (data.get("headers") or {}).items()
                }
                body_b64 = data.get("body_b64") or ""
                body_bytes = b""
                with contextlib.suppress(Exception):
                    body_bytes = base64.b64decode(body_b64)
                return status, headers_out, body_bytes
            finally:
                self._arm_idle()

    async def stream_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: str | None = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> _SessionStream:
        """Start ``method`` ``url`` inside the page; stream its body back.

        Returns after the header line lands, so ``status_code``/``headers``
        are readable immediately; ``aiter_content`` then yields the raw body
        as it crosses the pipe. Raises :class:`SessionTransportError` for
        pipe-level failures (dead helper, oversized frame, truncated body,
        header timeout) — the caller should treat those as fatal to the
        session — and leaves the session untouched for a plain HTTP status
        like 403. Only same-origin URLs are accepted.
        """
        validate_request_url(url)
        req_origin = _origin_of(url)
        if req_origin.lower() != self._origin.lower():
            raise SessionTransportError(
                f"webview session for {self._origin} cannot request {req_origin}"
            )
        stream = _SessionStream(self, timeout)
        self._stream_task = asyncio.create_task(
            self._run_stream(stream, method, url, headers, body, timeout)
        )
        try:
            await stream._await_headers()
        except BaseException:
            # The caller bailed before the header landed; don't orphan the
            # reader task (it would hold the session lock forever).
            reader, self._stream_task = self._stream_task, None
            if reader is not None and not reader.done():
                reader.cancel()
                with contextlib.suppress(BaseException):
                    await reader
            raise
        return stream

    async def _run_stream(
        self,
        stream: _SessionStream,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        body: str | None,
        timeout: float,
    ) -> None:
        """Frame one stream request: write, read header, stream the body.

        Holds the session lock for the *whole* exchange. The helper serves one
        response fully before reading the next request, so a second in-session
        request reading while a body streams would swallow body bytes as its
        own response line — serializing here is what keeps the pipe coherent.

        Pipe-level failures raise :class:`SessionTransportError` after tearing
        down the session; a plain HTTP status (e.g. 403) is delivered to the
        caller through ``stream`` and leaves the session untouched.
        """
        try:
            async with self._lock:
                self._cancel_idle()
                if self._proc is None or self._proc.returncode is not None:
                    raise SessionTransportError("webview session is not running")
                proc = self._proc
                if proc.stdin is None or proc.stdout is None:
                    raise SessionTransportError("webview session pipes are closed")

                self._next_id += 1
                req_id = self._next_id
                request = {
                    "id": req_id,
                    "method": method,
                    "url": url,
                    "headers": headers or {},
                    "body": body,
                    "stream": True,
                }
                try:
                    proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError) as exc:
                    await self._close_stream_transport()
                    raise SessionTransportError(
                        "webview session pipe closed"
                    ) from exc
                # Expose the header read as a cancellable task so close() can
                # interrupt a stream that never gets its header.
                header_task = asyncio.create_task(proc.stdout.readline())
                self._active_request = header_task

                try:
                    line = await asyncio.wait_for(header_task, timeout=timeout)
                except TimeoutError as exc:
                    raise SessionTransportError(
                        f"webview session timed out after {timeout:.0f}s"
                    ) from exc
                except ValueError as exc:
                    await self._close_stream_transport()
                    raise SessionTransportError("response frame too large") from exc
                finally:
                    if self._active_request is header_task:
                        self._active_request = None
                if not line:
                    await self._close_stream_transport()
                    raise SessionTransportError("webview session closed early")
                try:
                    data = json.loads(line.decode(errors="ignore"))
                except json.JSONDecodeError as exc:
                    await self._close_stream_transport()
                    raise SessionTransportError("malformed stream frame") from exc
                if not isinstance(data, dict) or data.get("id") != req_id:
                    await self._close_stream_transport()
                    raise SessionTransportError("unexpected response id")
                if data.get("error"):
                    await self._close_stream_transport()
                    raise SessionTransportError(str(data["error"]))
                status = data.get("status", 0)
                if not isinstance(status, int):
                    await self._close_stream_transport()
                    raise SessionTransportError("malformed stream status")
                length = data.get("length")
                if not isinstance(length, int) or length < 0:
                    await self._close_stream_transport()
                    raise SessionTransportError("malformed stream length")
                stream._store_header(
                    status,
                    {
                        str(k): str(v)
                        for k, v in (data.get("headers") or {}).items()
                    },
                )

                remaining = length
                while remaining > 0:
                    take = min(remaining, WEBVIEW_STREAM_CHUNK_BYTES)
                    try:
                        chunk = await asyncio.wait_for(
                            proc.stdout.readexactly(take), timeout=timeout
                        )
                    except asyncio.IncompleteReadError as exc:
                        await self._close_stream_transport()
                        raise SessionTransportError(
                            "webview stream truncated "
                            f"(expected {length} bytes)"
                        ) from exc
                    except TimeoutError as exc:
                        await self._close_stream_transport()
                        raise SessionTransportError(
                            f"webview stream timed out after {timeout:.0f}s"
                        ) from exc
                    remaining -= len(chunk)
                    stream._chunks.put_nowait(chunk)
                stream._chunks.put_nowait(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stream._fail(exc)
        finally:
            self._stream_task = None
            self._arm_idle()

    async def _close_stream_transport(self) -> None:
        """Close the session from inside the stream reader without self-cancel.

        ``close()`` cancels ``_stream_task`` — which, after a stream transport
        failure, *is* the currently running task. Detach first so the teardown
        is a normal close of the dead helper rather than a self-interrupt.
        """
        self._stream_task = None
        with contextlib.suppress(Exception):
            await self.close()

    def _arm_idle(self) -> None:
        """(Re)schedule shutdown after ``_idle_timeout`` seconds of no requests."""
        self._cancel_idle()
        if not self.alive or self._idle_timeout <= 0:
            return
        self._idle_task = asyncio.create_task(_idle_guard(self, self._idle_timeout))

    def _cancel_idle(self) -> None:
        """Cancel any pending idle shutdown without closing the session."""
        task, self._idle_task = self._idle_task, None
        if task is not None:
            task.cancel()

    async def close(self) -> None:
        """Shut down the helper process and cancel any in-flight request."""
        self._cancel_idle()
        proc, self._proc = self._proc, None
        stream_task, self._stream_task = self._stream_task, None
        if stream_task is not None:
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
        # Stop the stderr drainer; the process teardown below closes the pipe.
        stderr_task, self._stderr_task = self._stderr_task, None
        if stderr_task is not None:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
        # Cancel a waiting request so its readline() future resolves.
        if self._active_request is not None:
            self._active_request.cancel()
            self._active_request = None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.write(b'{"shutdown": true}\n')
                await asyncio.wait_for(proc.stdin.drain(), timeout=5)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except (TimeoutError, ProcessLookupError):
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()


# ---------------------------------------------------------------------------
# Global session management
# ---------------------------------------------------------------------------

# The global session and its lock are module-level singletons.  When the
# event loop is torn down (``asyncio.run()`` returns) the lock becomes
# invalid for a new loop, so we recreate it lazily.
_session: WebViewSession | None = None
_session_lock: asyncio.Lock | None = None


def _get_session_lock() -> asyncio.Lock:
    """Return the module-level lock, creating it for the current loop if needed."""
    global _session_lock
    # A Lock is bound to its running loop.  If the current event loop differs
    # from the one the lock was created for, replace it.
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    if _session_lock is None or (
        running_loop is not None and _session_lock._loop is not running_loop  # type: ignore[attr-defined]
    ):
        _session_lock = asyncio.Lock()
    return _session_lock


async def _idle_guard(session: WebViewSession, timeout: float) -> None:
    """Close ``session`` once it has been idle for ``timeout`` seconds."""
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return
    # Detach first so close()'s own _cancel_idle() doesn't cancel this task.
    session._idle_task = None
    # Only clear the global if this session still owns it; a newer session may
    # have replaced it while we slept.
    global _session
    if _session is session:
        _session = None
    with contextlib.suppress(Exception):
        await session.close()


def _alive_session_for(origin: str) -> WebViewSession | None:
    """The running session for ``origin``, or ``None``. Never spawns."""
    session = _session
    if session is not None and session._origin == origin and session.alive:
        return session
    return None


def live_session_for(base_url: str) -> WebViewSession | None:
    """The session already authenticated for ``base_url``'s origin, if alive.

    Unlike :func:`ensure_session` this never starts a helper, so the downloader
    can reuse an authenticated session without paying for a new solve.
    """
    return _alive_session_for(_origin_of(base_url))


async def ensure_session(base_url: str) -> WebViewSession | None:
    """Start (or reuse) the long-lived session for ``base_url``'s host.

    Returns ``None`` when the session cannot run in this environment, so
    callers can fall back to plain HTTP. The session stays alive until it has
    been idle for ``WEBVIEW_IDLE_TIMEOUT`` seconds or :func:`close_session` is
    called, so a scraper can fan out API and download requests without
    re-solving the challenge.

    The session preserves the scheme and port of ``base_url`` so that
    ``https://example.com`` and ``https://example.com:8443`` are treated as
    different origins.
    """
    global _session
    origin = _origin_of(base_url)
    if _alive_session_for(origin) is not None:
        return _session
    if _session is not None:
        await _session.close()
        _session = None
    if not available():
        return None
    lock = _get_session_lock()
    async with lock:
        if _alive_session_for(origin) is not None:
            return _session
        session = WebViewSession(base_url)
        if not await session.start():
            _session = None
            return None
        _session = session
    return _session


async def close_session() -> None:
    """Shut down the long-lived session if one is running."""
    global _session
    session, _session = _session, None
    if session is not None:
        await session.close()


async def session_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout: float = REQUEST_TIMEOUT,
) -> tuple[int, dict[str, str], bytes]:
    """Convenience wrapper around :func:`ensure_session` + ``session.request``.

    Raises :class:`SessionUnavailableError` when no session can be started and
    :class:`SessionRequestError` when an in-page request fails — callers that
    have a plain-HTTP fallback should catch these.
    """
    session = await ensure_session(url)
    if session is None:
        raise SessionUnavailableError(
            f"webview session unavailable for {urlsplit(url).hostname}"
        )
    return await session.request(
        method, url, headers=headers, body=body, timeout=timeout
    )


def session_enabled() -> bool:
    """True when the webview request session is the intended transport.

    Mirrors the challenge-solver gating: the session is only used when a
    webview can actually run here *and* the user has not pinned the solver to
    ``impersonation``/``off`` (both of which mean "no browser window"). This
    lets scrapers fall back to plain HTTP in headless CI or when the user
    explicitly disabled the webview.
    """
    if not available():
        return False
    with contextlib.suppress(Exception):
        from .cf import solver_mode

        return solver_mode() in ("auto", "webview")
    return True

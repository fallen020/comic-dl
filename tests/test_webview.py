"""Unit tests for the webview session parent (:mod:`comic_dl.webview`).

The GUI helper cannot run headless in CI, so request/close behavior is pinned
in-process with fake subprocess handles while the pure helpers (origin
parsing, availability, helper env/argv) run against mocks.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

import comic_dl.webview as webview_mod
from comic_dl.webview import (
    SessionRequestError,
    SessionTransportError,
    WebViewSession,
    _origin_of,
)


class TestOriginOf:
    def test_plain_https(self):
        assert _origin_of("https://example.com/") == "https://example.com"

    def test_default_https_port_normalized_away(self):
        assert _origin_of("https://example.com:443/x") == "https://example.com"

    def test_default_http_port_normalized_away(self):
        assert _origin_of("http://example.com:80/") == "http://example.com"

    def test_non_default_port_kept(self):
        assert _origin_of("https://example.com:8443/") == "https://example.com:8443"

    def test_scheme_and_host_lowercased(self):
        assert _origin_of("HTTPS://Example.COM:443/") == "https://example.com"


class _Proc:
    def __init__(self, returncode):
        self.returncode = returncode


class TestAvailable:
    def test_false_when_pywebview_missing(self, monkeypatch):
        monkeypatch.setattr(webview_mod.importlib.util, "find_spec", lambda _name: None)
        assert webview_mod.available() is False

    def test_false_without_display_or_xvfb(self, monkeypatch):
        monkeypatch.setattr(
            webview_mod.importlib.util, "find_spec", lambda _name: object()
        )
        monkeypatch.setattr(webview_mod.os, "name", "posix")
        monkeypatch.setattr(webview_mod.sys, "platform", "linux")
        monkeypatch.setattr(
            webview_mod.subprocess, "run", lambda *a, **k: _Proc(returncode=0)
        )
        monkeypatch.setattr(webview_mod, "_has_display", lambda: False)
        monkeypatch.setattr(webview_mod.shutil, "which", lambda _name: None)
        assert webview_mod.available() is False

    def test_true_with_display(self, monkeypatch):
        monkeypatch.setattr(
            webview_mod.importlib.util, "find_spec", lambda _name: object()
        )
        monkeypatch.setattr(webview_mod.os, "name", "posix")
        monkeypatch.setattr(webview_mod.sys, "platform", "linux")
        monkeypatch.setattr(
            webview_mod.subprocess, "run", lambda *a, **k: _Proc(returncode=0)
        )
        monkeypatch.setattr(webview_mod, "_has_display", lambda: True)
        assert webview_mod.available() is True

    def test_false_when_gi_probe_fails(self, monkeypatch):
        monkeypatch.setattr(
            webview_mod.importlib.util, "find_spec", lambda _name: object()
        )
        monkeypatch.setattr(webview_mod.os, "name", "posix")
        monkeypatch.setattr(webview_mod.sys, "platform", "linux")
        monkeypatch.setattr(
            webview_mod.subprocess, "run", lambda *a, **k: _Proc(returncode=1)
        )
        monkeypatch.setattr(webview_mod, "_has_display", lambda: True)
        assert webview_mod.available() is False

    def test_true_when_system_python_has_gi_but_venv_lacks_it(self, monkeypatch):
        # A venv python without PyGObject must not disable the solver when a
        # candidate the helper would spawn (a system python3) has gi.
        monkeypatch.setattr(
            webview_mod.importlib.util, "find_spec", lambda _name: object()
        )
        monkeypatch.setattr(webview_mod.os, "name", "posix")
        monkeypatch.setattr(webview_mod.sys, "platform", "linux")
        monkeypatch.setattr(webview_mod, "_has_display", lambda: True)
        monkeypatch.setattr(
            webview_mod,
            "_interpreter_candidates",
            lambda: [sys.executable, "/usr/bin/python3"],
        )

        def fake_run(cmd, **kwargs):
            return _Proc(returncode=0 if cmd[0] != sys.executable else 1)

        monkeypatch.setattr(webview_mod.subprocess, "run", fake_run)
        assert webview_mod.available() is True


class TestHelperEnv:
    def test_includes_source_root_in_pythonpath(self):
        env = webview_mod._helper_env()
        import comic_dl

        src_root = os.path.dirname(os.path.dirname(os.path.abspath(comic_dl.__file__)))
        assert src_root in env.get("PYTHONPATH", "").split(os.pathsep)


class TestHelperCommand:
    def test_uses_current_interpreter_when_gi_probe_passes(self, monkeypatch):
        probes = []

        def fake_run(cmd, **kwargs):
            probes.append(cmd[0])
            return _Proc(returncode=0)

        monkeypatch.setattr(webview_mod.os, "name", "posix")
        monkeypatch.setattr(webview_mod, "_python_on_path", lambda: [])
        monkeypatch.setattr(webview_mod.subprocess, "run", fake_run)
        cmd = webview_mod._helper_command()
        assert cmd == [sys.executable, "-m", webview_mod._HELPER_MODULE]
        assert probes[0] == sys.executable

    def test_none_when_no_interpreter_passes_gi_probe(self, monkeypatch):
        monkeypatch.setattr(webview_mod.os, "name", "posix")
        monkeypatch.setattr(webview_mod, "_python_on_path", lambda: [])
        monkeypatch.setattr(
            webview_mod.subprocess, "run", lambda *a, **k: _Proc(returncode=1)
        )
        assert webview_mod._helper_command() is None


class _NoopStdin:
    def write(self, _data):
        pass

    async def drain(self):
        return None


class _OversizedReadline:
    async def readline(self):
        raise ValueError("Separator is not found, and chunk exceed the limit")


class _OversizedProc:
    returncode = None

    def __init__(self):
        self.stdin = _NoopStdin()
        self.stdout = _OversizedReadline()

    async def wait(self):
        self.returncode = 0
        return 0


class _BlockingReadline:
    async def readline(self):
        await asyncio.Future()


class _BlockingProc:
    returncode = None

    def __init__(self):
        self.stdin = _NoopStdin()
        self.stdout = _BlockingReadline()

    async def wait(self):
        self.returncode = 0
        return 0


class TestWebViewSessionErrors:
    pytestmark = pytest.mark.asyncio

    async def test_oversized_frame_maps_to_session_request_error(self, monkeypatch):
        # readline() raises ValueError for frames over the pipe limit; the
        # parent must fold that into a SessionRequestError and tear down the
        # session (the reader's buffer is left in an undefined state).
        monkeypatch.setattr(webview_mod, "validate_request_url", lambda url: url)
        sess = WebViewSession("https://example.com/")
        sess._proc = _OversizedProc()
        with pytest.raises(SessionRequestError, match="frame too large"):
            await sess.request("GET", "https://example.com/x")
        assert sess.alive is False

    async def test_close_cancels_in_flight_request(self, monkeypatch):
        monkeypatch.setattr(webview_mod, "validate_request_url", lambda url: url)
        sess = WebViewSession("https://example.com/")
        sess._proc = _BlockingProc()
        req_task = asyncio.create_task(
            sess.request("GET", "https://example.com/x")
        )
        for _ in range(100):
            if sess._active_request is not None:
                break
            await asyncio.sleep(0.01)
        assert sess._active_request is not None
        await sess.close()
        with pytest.raises(asyncio.CancelledError):
            await req_task


class TestIdleShutdown:
    pytestmark = pytest.mark.asyncio

    async def test_idle_timer_closes_session(self, monkeypatch):
        monkeypatch.setattr(webview_mod, "validate_request_url", lambda url: url)
        sess = WebViewSession("https://example.com/")
        sess._proc = _BlockingProc()
        sess._idle_timeout = 0.02
        sess._arm_idle()
        await asyncio.sleep(0.05)
        assert sess.alive is False
        assert sess._idle_task is None

    async def test_new_request_cancels_pending_idle(self, monkeypatch):
        # A request must cancel the armed timer so it can never fire mid-flight.
        monkeypatch.setattr(webview_mod, "validate_request_url", lambda url: url)
        sess = WebViewSession("https://example.com/")
        sess._proc = _BlockingProc()
        sess._idle_timeout = 0.02
        sess._arm_idle()
        assert sess._idle_task is not None
        req_task = asyncio.create_task(
            sess.request("GET", "https://example.com/x")
        )
        for _ in range(100):
            if sess._active_request is not None:
                break
            await asyncio.sleep(0.01)
        # Even though the old idle deadline passes while we're reading, the
        # session must survive: the timer was cancelled at request start.
        await asyncio.sleep(0.05)
        assert sess._idle_task is None
        assert sess.alive is True
        await sess.close()
        with pytest.raises(asyncio.CancelledError):
            await req_task

    async def test_close_cancels_pending_idle(self, monkeypatch):
        sess = WebViewSession("https://example.com/")
        sess._proc = _BlockingProc()
        sess._idle_timeout = 0.02
        sess._arm_idle()
        await sess.close()
        assert sess._idle_task is None

    async def test_idle_guard_clears_global_session(self, monkeypatch):
        monkeypatch.setattr(webview_mod, "_session", None)
        sess = WebViewSession("https://example.com/")
        sess._proc = _BlockingProc()
        sess._idle_timeout = 0.02
        webview_mod._session = sess
        sess._arm_idle()
        await asyncio.sleep(0.05)
        assert webview_mod._session is None


@pytest.fixture(autouse=True)
def _isolate_streams(monkeypatch):
    monkeypatch.setattr(webview_mod, "_session", None)


_VALID_STREAM_HEADER = (
    b'{"id": 1, "status": 200, "headers": {}, "stream": true, "length": 10}\n'
)


class _TruncatedStdout:
    async def readline(self):
        return _VALID_STREAM_HEADER

    async def readexactly(self, n):
        raise asyncio.IncompleteReadError(b"", n)


class _TruncatedProc:
    returncode = None

    def __init__(self):
        self.stdin = _NoopStdin()
        self.stdout = _TruncatedStdout()

    async def wait(self):
        self.returncode = 0
        return 0


class TestStreamRequest:
    @pytest.mark.asyncio
    async def test_truncated_body_raises_and_discards_session(self, monkeypatch):
        # The helper cropped the body: the parent must surface an explicit
        # truncated-download error (never a short image) and tear the session
        # down, since the pipe is mid-body and cannot be reused.
        monkeypatch.setattr(webview_mod, "validate_request_url", lambda url: url)
        sess = WebViewSession("https://example.com/")
        sess._proc = _TruncatedProc()
        resp = await sess.stream_request("GET", "https://example.com/x")
        with pytest.raises(SessionTransportError, match="truncated"):
            async for _ in resp.aiter_content():
                pass
        assert sess.alive is False

    def test_raise_for_status_raises_http_error(self):
        stream = webview_mod._SessionStream.__new__(webview_mod._SessionStream)
        stream.status_code = 404
        stream.headers = {}
        with pytest.raises(Exception, match="HTTP Error 404"):
            stream.raise_for_status()
        stream.status_code = 200
        stream.raise_for_status()  # must not raise


class TestLiveSessionFor:
    def test_returns_alive_same_origin_session(self, monkeypatch):
        monkeypatch.setattr(webview_mod, "_session", None)
        sess = WebViewSession("https://example.com/")
        sess._proc = _Proc(returncode=None)
        webview_mod._session = sess
        assert webview_mod.live_session_for("https://example.com/x") is sess

    def test_none_for_dead_session(self, monkeypatch):
        monkeypatch.setattr(webview_mod, "_session", None)
        sess = WebViewSession("https://example.com/")
        sess._proc = _Proc(returncode=1)
        webview_mod._session = sess
        assert webview_mod.live_session_for("https://example.com/x") is None

    def test_none_for_different_origin(self, monkeypatch):
        monkeypatch.setattr(webview_mod, "_session", None)
        sess = WebViewSession("https://example.com/")
        sess._proc = _Proc(returncode=None)
        webview_mod._session = sess
        assert webview_mod.live_session_for("https://other.example/x") is None

    def test_none_without_global(self, monkeypatch):
        monkeypatch.setattr(webview_mod, "_session", None)
        assert webview_mod.live_session_for("https://example.com/x") is None

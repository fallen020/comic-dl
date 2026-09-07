"""Tests for the webview_solver ↔ WebViewSession protocol.

The GUI/webkit helper cannot run headless in CI, so the wire format is pinned
two ways:

* ``_handle_request`` — the pure request→response mapping — against a fake
  window (no subprocess, no JS engine).
* a real subprocess contract test: the client (``WebViewSession``) is driven
  against a fake serve process that implements the *documented* stdin/stdout
  JSON-lines protocol, so a drift in either side breaks the test offline.
"""

from __future__ import annotations

import sys

import pytest

from comic_dl.webview import (
    SessionRequestError,
    SessionTransportError,
    WebViewSession,
)
from comic_dl.webview_solver import _emit_stream, _handle_request, _xhr_js


class TestXhrJsEscaping:
    def test_method_url_headers_body_are_json_escaped(self):
        js = _xhr_js(
            'GET',
            'https://example.com/a?q="x"',
            {"X-Odd": 'say "hi" \\ back'},
            'body "text"',
        )
        # Values must be embedded via json.dumps, so quotes/backslashes in the
        # inputs cannot break out of the generated JS string literals.
        assert '"https://example.com/a?q=\\"x\\""' in js
        assert '"say \\"hi\\" \\\\ back"' in js
        assert '"body \\"text\\""' in js

    def test_none_body_emits_null_literal(self):
        js = _xhr_js("GET", "https://example.com", {}, None)
        assert "xhr.send(null);" in js

    def test_pre_flattened_json_body_is_kept_as_string_literal(self):
        # Dict bodies are normalized to a JSON *string* before _xhr_js is
        # called (in _handle_request), so a raw JSON string here must end up
        # quoted as a JS string literal, with its inner quotes escaped.
        js = _xhr_js("POST", "https://example.com", {}, '{"k": "v"}')
        assert 'xhr.send("{\\"k\\": \\"v\\"}");' in js

    def test_method_stringified(self):
        js = _xhr_js("post", "https://example.com", {}, None)
        assert '"post"' in js


class _FakeWindow:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def evaluate_js(self, js):
        self.calls.append(js)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class TestHandleRequest:
    def test_maps_payload_into_response(self):
        window = _FakeWindow(
            '{"status": 200, "headers": {"content-type": "image/jpeg"}, '
            '"body_b64": "aGVsbG8="}'
        )
        resp = _handle_request(window, {"id": 7, "url": "https://x/"}, "https://x")
        assert resp == {
            "id": 7,
            "status": 200,
            "headers": {"content-type": "image/jpeg"},
            "body_b64": "aGVsbG8=",
        }
        assert "error" not in resp
        assert "XMLHttpRequest" in window.calls[0]

    def test_in_page_error_is_passed_through(self):
        window = _FakeWindow('{"status": 0, "error": "blocked by CSP"}')
        resp = _handle_request(window, {"id": 3, "url": "https://x/"}, "https://x")
        assert resp["error"] == "blocked by CSP"
        assert resp["status"] == 0

    def test_non_string_eval_result_becomes_empty_response(self):
        window = _FakeWindow(None)
        resp = _handle_request(window, {"id": 1, "url": "https://x/"}, "https://x")
        assert resp == {"id": 1, "status": 0, "headers": {}, "body_b64": ""}

    def test_transport_exception_folded_into_status_zero(self):
        window = _FakeWindow(RuntimeError("eval exploded"))
        resp = _handle_request(window, {"id": 9, "url": "https://x/"}, "https://x")
        assert resp["id"] == 9
        assert resp["status"] == 0
        assert resp["error"] == "RuntimeError: eval exploded"

    def test_shutdown_returns_none(self):
        assert _handle_request(_FakeWindow("{}"), {"shutdown": True}, "https://x") is None

    def test_dict_body_is_flattened_to_json_string(self):
        window = _FakeWindow('{"status": 200}')
        _handle_request(
            window, {"id": 1, "url": "https://x/", "method": "POST", "body": {"a": 1}},
            "https://x",
        )
        assert 'xhr.send("{\\"a\\": 1}");' in window.calls[0]

    def test_cross_origin_request_is_blocked(self):
        window = _FakeWindow('{"status": 200}')
        resp = _handle_request(
            window, {"id": 2, "url": "https://evil.example/x"}, "https://x"
        )
        assert resp["status"] == 0
        assert "cross-origin" in resp["error"]

    def test_stream_request_uses_binary_xhr(self):
        # Binary payloads must survive: the stream path forces the response
        # through charset=x-user-defined and maps code points straight to
        # bytes (sync XHR cannot use responseType=arraybuffer).
        window = _FakeWindow('{"status": 200, "body_b64": "AAEC/w=="}')
        _handle_request(
            window, {"id": 5, "url": "https://x/", "stream": True}, "https://x"
        )
        js = window.calls[0]
        assert "x-user-defined" in js
        assert "& 0xFF" in js
        assert "getAllResponseHeaders" in js


class TestEmitStream:
    def test_writes_header_line_then_exact_body_bytes(self):
        class _Buf:
            def __init__(self):
                self.data = b""

            def write(self, b):
                self.data += b

            def flush(self):
                pass

        import base64
        import json

        body = b"\x00\x01\x02\xff"
        buf = _Buf()
        _emit_stream(
            {
                "id": 3,
                "status": 200,
                "headers": {"content-type": "image/jpeg"},
                "body_b64": base64.b64encode(body).decode(),
            },
            buf,
        )
        header_line, sep, raw = buf.data.partition(b"\n")
        assert sep == b"\n"
        header = json.loads(header_line.decode("utf-8"))
        assert header["id"] == 3
        assert header["stream"] is True
        assert header["length"] == len(body)
        assert raw == body

    def test_header_flushed_before_body(self):
        # If the header weren't flushed first, the parent would deadlock
        # waiting for a header line that still shares the buffer with the
        # body. Track the flush count at the moment each write lands.
        events = []

        class _Buf:
            def flush(self):
                events.append(("flush",))

            def write(self, b):
                events.append(("write", b, len([e for e in events if e[0] == "flush"])))

        buf = _Buf()
        _emit_stream({"id": 1, "status": 200, "body_b64": ""}, buf)
        header_write = events[0]
        assert header_write[0] == "write"
        assert header_write[1].endswith(b"\n")
        assert header_write[2] == 0
        assert any(e[0] == "flush" for e in events[1:])


def _fake_serve_streaming() -> str:
    """A serve helper that also implements the stream frame protocol."""
    return r"""
import json
import sys


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


out({"ready": True, "cookies": []})
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    if req.get("shutdown"):
        break
    if not req.get("stream"):
        out({
            "id": req.get("id"),
            "status": 200,
            "headers": {"x-fake": "yes"},
            "body_b64": __import__("base64").b64encode(
                ("ok:" + str(req.get("method", ""))).encode()
            ).decode(),
        })
        continue
    body = b"streamed:" + str(req.get("method", "")).encode()
    out({"id": req.get("id"), "status": 200, "headers": {"x-fake": "yes"},
         "stream": True, "length": len(body)})
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()
"""


FAKE_SERVE = r"""
import json
import sys


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


out({"ready": True, "cookies": []})
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    if req.get("shutdown"):
        break
    if req.get("method") == "FAIL":
        out({"id": req.get("id"), "status": 0, "headers": {},
             "body_b64": "", "error": "boom"})
        continue
    out({
        "id": req.get("id"),
        "status": 200,
        "headers": {"x-fake": "yes"},
        "body_b64": __import__("base64").b64encode(
            ("ok:" + str(req.get("method", ""))).encode()
        ).decode(),
    })
"""


class TestWebViewSessionWireProtocol:
    """Drive WebViewSession against a fake serve process implementing the
    documented wire protocol — covers start handshake, request/response match
    by id, error passthrough, and shutdown teardown."""

    pytestmark = pytest.mark.asyncio

    @staticmethod
    def _patch_helper(monkeypatch):
        import comic_dl.webview as webview_mod

        monkeypatch.setattr(
            webview_mod, "_helper_command", lambda: [sys.executable, "-c", FAKE_SERVE]
        )
        # The fake never talks to a real host; skip the DNS/SSRF gate.
        monkeypatch.setattr(webview_mod, "validate_request_url", lambda url: url)

    async def test_start_request_close_round_trip(self, monkeypatch):
        self._patch_helper(monkeypatch)
        sess = WebViewSession("https://example.com/")
        try:
            assert await sess.start() is True
            assert sess.alive is True
            status, headers, body = await sess.request("GET", "https://example.com/foo")
            assert status == 200
            assert headers == {"x-fake": "yes"}
            assert body == b"ok:GET"
            _, _, body = await sess.request(
                "POST", "https://example.com/sub", body="payload"
            )
            assert body == b"ok:POST"
        finally:
            await sess.close()
        assert sess.alive is False

    async def test_in_page_error_raises_session_request_error(self, monkeypatch):
        self._patch_helper(monkeypatch)
        sess = WebViewSession("https://example.com/")
        try:
            assert await sess.start() is True
            with pytest.raises(SessionRequestError, match="boom"):
                await sess.request("FAIL", "https://example.com/x")
        finally:
            await sess.close()

    async def test_stream_request_round_trip_via_wire(self, monkeypatch):
        # The parent drives its documented client against a serve helper that
        # ships a header line followed by raw body bytes — no b64 anywhere in
        # the transport path.
        import comic_dl.webview as webview_mod

        monkeypatch.setattr(
            webview_mod,
            "_helper_command",
            lambda: [sys.executable, "-c", _fake_serve_streaming()],
        )
        monkeypatch.setattr(webview_mod, "validate_request_url", lambda url: url)
        sess = WebViewSession("https://example.com/")
        try:
            assert await sess.start() is True
            resp = await sess.stream_request("GET", "https://example.com/foo")
            assert resp.status_code == 200
            assert resp.headers == {"x-fake": "yes"}
            parts = [chunk async for chunk in resp.aiter_content()]
            assert b"".join(parts) == b"streamed:GET"
            # The stream must not have poisoned the pipe: a plain request
            # still round-trips on the same session.
            _, _, body = await sess.request("GET", "https://example.com/bar")
            assert body == b"ok:GET"
        finally:
            await sess.close()

    async def test_stream_request_cross_host_rejected_without_pipeline(self):
        sess = WebViewSession("https://example.com/")
        assert sess._proc is None
        with pytest.raises(SessionTransportError):
            await sess.stream_request("GET", "https://other.example/x")

    async def test_cross_host_request_rejected_without_pipeline(self):
        # Same-host enforcement happens before the subprocess exists: a wrong
        # host must be refused without ever spawning a helper.
        sess = WebViewSession("https://example.com/")
        assert sess._proc is None
        with pytest.raises(SessionRequestError):
            await sess.request("GET", "https://other.example/x")


def test_body_b64_decode_round_trip():
    from comic_dl.webview_solver import _decode_body

    assert _decode_body("") == b""
    assert _decode_body(None) == b""
    assert _decode_body("aGVsbG8=") == b"hello"
    assert _decode_body("!!!not-base64!!!") == b""


class TestMainURLValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "javascript:void(0)",
            "http://127.0.0.1/x",
        ],
    )
    def test_blocks_unsafe_url_before_webview(self, monkeypatch, url):
        from comic_dl.webview_solver import main

        monkeypatch.setattr("sys.argv", ["webview_solver", "--url", url])
        assert main() == 1

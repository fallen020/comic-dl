"""Tiny dependency-free HTTP test server.

Binds an asyncio TCP server to 127.0.0.1 on an ephemeral port. Routes map a
path to ``(status, headers, body)``. Used by the security tests to exercise
real sockets (offline) for redirect/SSRF behaviour.
"""

from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlparse


class NetResponse:
    """Minimal HTTP response shaped like a streaming response."""

    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status_code = status
        self.headers = headers
        self._body = body
        self._sent = False

    @property
    def content(self) -> bytes:
        return self._body

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    async def aiter_content(self, chunk_size: int | None = None) -> bytes:
        if not self._sent:
            self._sent = True
            yield self._body

    async def close(self) -> None:
        self._sent = True

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

            raise CurlHTTPError(f"HTTP error {self.status_code}", response=self)


class NetHttpClient:
    """Blocking-free, offline-control lite HTTP client (one GET per call).

    Intentionally ignores redirects (caller follows them manually), matching
    the app's download path.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    def stream(self, method: str, url: str, **kwargs):
        return self._request(url)

    async def get(self, url: str, **kwargs):
        return await self._request(url)

    async def head(self, url: str, **kwargs):
        return await self._request(url)

    async def _request(self, url: str) -> NetResponse:
        parsed = urlparse(url)
        host = parsed.hostname or self.host
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        reader, writer = await asyncio.open_connection(host, port)
        try:
            writer.write(
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            raw = b""
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                raw += chunk
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

        head, _sep, body = raw.partition(b"\r\n\r\n")
        status = 0
        headers: dict[str, str] = {}
        for i, line in enumerate(head.decode(errors="replace").split("\r\n")):
            if i == 0:
                status = int(line.split(" ", 2)[1])
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return NetResponse(status, headers, body)


class FakeHttpServer:
    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes]]):
        self.routes = routes
        self.port: int = 0
        self.host = "127.0.0.1"
        self.hits: list[str] = []
        self._server: asyncio.AbstractServer | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def __aenter__(self) -> FakeHttpServer:
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                data += chunk
            reqline = data.split(b"\r\n", 1)[0].decode(errors="replace")
            parts = reqline.split(" ") if reqline else []
            path = parts[1] if len(parts) > 1 else "/"
            self.hits.append(path)
            if path not in self.routes:
                status, headers, body = 404, {}, b"not found"
            else:
                status, headers, body = self.routes[path]
            resp = f"HTTP/1.1 {status}\r\n"
            for k, v in headers.items():
                resp += f"{k}: {v}\r\n"
            resp += f"Content-Length: {len(body)}\r\n\r\n"
            writer.write(resp.encode() + body)
            await writer.drain()
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

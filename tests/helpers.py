from __future__ import annotations

import json


class MockResponse:
    """A minimal offline stand-in for a ``curl_cffi`` response.

    Carries the attributes scrapers read (``content``, ``text``,
    ``status_code``, ``ok``, ``headers``, ``url``) plus a ``json()`` helper.
    """

    def __init__(
        self,
        content: bytes | str = b"",
        status: int = 200,
        json_data: dict | None = None,
    ):
        self.content = content if isinstance(content, bytes) else content.encode()
        self.text = self.content.decode()
        self.status_code = status
        self.ok = status < 400
        self.headers = {}
        self.url = ""
        self._json_data = json_data

    def raise_for_status(self):
        if not self.ok:
            from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

            raise CurlHTTPError(f"HTTP Error {self.status_code}", response=self)

    def json(self):
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockSession:
    """An async session whose per-URL handler returns responses.

    The handler may return either a :class:`MockResponse` or a
    ``(status, content)`` tuple. Tracks ``_handler_calls`` and a ``requests``
    log so cache and politeness behaviour can be asserted.
    """

    def __init__(self, handler):
        self._handler = handler
        self._handler_calls = 0
        self.requests: list[tuple[str, dict]] = []

    async def get(self, url: str, **kwargs) -> MockResponse:
        self._handler_calls += 1
        self.requests.append((url, kwargs))
        return self._as_response(self._handler(url))

    async def head(self, url: str, **kwargs) -> MockResponse:
        return await self.get(url, **kwargs)

    async def post(self, url: str, **kwargs) -> MockResponse:
        self._handler_calls += 1
        self.requests.append((url, kwargs))
        return self._as_response(self._handler(url))

    async def __aenter__(self) -> MockSession:
        return self

    async def __aexit__(self, *args):
        pass

    @staticmethod
    def _as_response(result):
        if isinstance(result, tuple):
            return MockResponse(result[1], result[0])
        return result

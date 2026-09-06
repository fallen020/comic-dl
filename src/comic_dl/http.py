"""Shared curl_cffi session helpers and the persistent cookie-jar access layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from .config import http_setting
from .cookies import CookieJar

_JAR: CookieJar | None = None


def cookie_jar_enabled() -> bool:
    """Whether the persistent cookie jar is active (``[http] cookie-jar``).

    Defaults to on. ``--no-cookie`` / ``[http] cookie-jar = false`` makes a
    run fully stateless.
    """
    return bool(http_setting("cookie-jar", True))


def get_jar() -> CookieJar | None:
    """Shared process-wide cookie jar, or ``None`` when disabled."""
    global _JAR
    if not cookie_jar_enabled():
        return None
    if _JAR is None:
        _JAR = CookieJar()
    return _JAR


def jar_cookies_for(url: str) -> dict[str, str]:
    """Non-expired jar cookies for the host of ``url`` (as name→value)."""
    jar = get_jar()
    host = urlsplit(url).hostname
    if jar is None or not host:
        return {}
    return jar.cookies_for(host)


def jar_cookies_kwargs(url: str) -> dict[str, Any]:
    """Splat-able per-request ``cookies=`` kwarg for ``url``'s host.

    Empty when the jar is disabled or holds nothing for that host, so call
    sites can do ``await req(url, **jar_cookies_kwargs(url))`` unconditionally.
    """
    cookies = jar_cookies_for(url)
    return {"cookies": cookies} if cookies else {}


def absorb_response_cookies(client: Any, headers: Mapping[str, Any] | None = None) -> None:
    """Persist every cookie the session has received so far.

    curl_cffi keeps a full RFC cookie jar on the session
    (``client.cookies.jar``) carrying domain/path/expiry; we snapshot it into
    the persistent store after each response. Safe to call repeatedly.

    When ``headers`` is given and carries no ``Set-Cookie`` header, the
    snapshot is skipped entirely — the jar cannot have changed — so image
    downloads that never set cookies avoid a SQLite round-trip per page.
    """
    if headers is not None and not _response_has_set_cookie(headers):
        return
    jar = get_jar()
    if jar is None:
        return
    cookiejar = getattr(getattr(client, "cookies", None), "jar", None)
    if cookiejar is not None:
        jar.store_cookiejar(cookiejar)


def _response_has_set_cookie(headers: Mapping[str, Any] | None) -> bool:
    if not headers:
        return False
    return any(name.lower() == "set-cookie" for name in headers)

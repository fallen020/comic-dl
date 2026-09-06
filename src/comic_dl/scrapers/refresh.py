"""Stale-image-link refreshers.

Some sites hand out time-limited image links (e-hentai's H@H ``keystamp``
URLs). When a download retry hits an expired link, re-fetching the *same*
URL can never succeed; the link must be regenerated from the page that
minted it. An :class:`ImageItem` therefore carries its provenance in
``source_url`` and a refresher — registered per source host here — turns
that page back into a fresh image URL.

Only e-hentai implements a refresher today. The registry is deliberately
shaped so a future scraper only adds a decorated function; the downloader
stays ignorant of site specifics.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from curl_cffi.requests import AsyncSession

    from ..models import ImageItem

Refresher = Callable[["AsyncSession", "ImageItem"], Awaitable["ImageItem | None"]]

# source-page hostname (lowercased) -> refresher
_REFRESHERS: dict[str, Refresher] = {}


def register_image_refresher(*domains: str) -> Callable[[Refresher], Refresher]:
    """Register an async ``(client, item) -> ImageItem | None`` refresher.

    ``None`` (or a raised error, handled by the caller) means "could not
    produce a different URL" — the downloader then retries the original
    link as before.
    """

    def deco(fn: Refresher) -> Refresher:
        for domain in domains:
            _REFRESHERS[domain.lower()] = fn
        return fn

    return deco


async def refresh_image_url(client: AsyncSession, item: ImageItem) -> ImageItem | None:
    """Return ``item`` re-pointed at a fresh image URL, or ``None``.

    Dispatches on the host of ``item.source_url``. No registration (or any
    failure inside the refresher) yields ``None`` so callers fall back to
    plain same-URL retries.
    """
    if not item.source_url:
        return None
    host = (urlsplit(item.source_url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    refresher = _REFRESHERS.get(host)
    if refresher is None:
        return None
    try:
        refreshed = await refresher(client, item)
    except Exception:
        # A refresh failure must never abort the download loop; the caller
        # simply retries whatever link it has.
        return None
    if refreshed is None or refreshed.url == item.url:
        return None
    return refreshed

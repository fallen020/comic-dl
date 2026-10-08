"""FSI Comics ES (es.fsicomics.com) — Spanish mirror.

Same catalogue and Foxiz markup as the apex; only the localized title format,
chapter words and path segments differ, all handled by the shared scraper.
"""

from __future__ import annotations

from ...registry import register_scraper
from ._base import FsicomixScraper

DOMAIN = "es.fsicomics.com"


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class FsicomicsEsScraper(FsicomixScraper):
    """FSI Comics ES chapter and series scraper."""

    domain = DOMAIN
    name = "fsicomics-es"
    site_id = "fsicomics-es"
    display_name = "FSI Comics ES"
    chapter_url_pattern = "/{comic-slug}/"
    series_url_pattern = "/{category}/{artist}/"
    version = "2.0.0"
    test_url = "https://es.fsicomics.com/a-night-with-loona-capitulo-3-jizoku/"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"
    language = "es"
    base = "https://es.fsicomics.com"

"""FSI Comics DE (de.fsicomics.com) — German mirror.

Same catalogue and Foxiz markup as the apex; only the localized title format,
chapter words and path segments differ, all handled by the shared scraper.
"""

from __future__ import annotations

from ...registry import register_scraper
from ._base import FsicomixScraper

DOMAIN = "de.fsicomics.com"


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class FsicomicsDeScraper(FsicomixScraper):
    """FSI Comics DE chapter and series scraper."""

    domain = DOMAIN
    name = "fsicomics-de"
    site_id = "fsicomics-de"
    display_name = "FSI Comics DE"
    content_warning = "nsfw"
    chapter_url_pattern = "/{comic-slug}/"
    series_url_pattern = "/{category}/{artist}/"
    version = "2.0.0"
    test_url = "https://de.fsicomics.com/emmas-corruption-kapitel-10-antalore42/"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"
    language = "de"
    base = "https://de.fsicomics.com"

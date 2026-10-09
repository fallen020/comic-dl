"""FSI Comics FR (fr.fsicomics.com) — French mirror.

Same catalogue and Foxiz markup as the apex; only the localized title format,
chapter words and path segments differ, all handled by the shared scraper.
"""

from __future__ import annotations

from ...registry import register_scraper
from ._base import FsicomixScraper

DOMAIN = "fr.fsicomics.com"


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class FsicomicsFrScraper(FsicomixScraper):
    """FSI Comics FR chapter and series scraper."""

    domain = DOMAIN
    name = "fsicomics-fr"
    site_id = "fsicomics-fr"
    display_name = "FSI Comics FR"
    content_warning = "nsfw"
    chapter_url_pattern = "/{comic-slug}/"
    series_url_pattern = "/{category}/{artist}/"
    version = "2.0.0"
    test_url = "https://fr.fsicomics.com/epouse-pervertie-chapitre-13-historikito/"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"
    language = "fr"
    base = "https://fr.fsicomics.com"

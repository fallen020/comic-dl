"""FSI Comics apex host (fsicomics.com), English catalogue.

Rolled into the ``fsicomics`` package so all five FSI hosts share one module
tree; see ``_base`` for the shared rules.
"""

from __future__ import annotations

from ...registry import register_scraper
from ._base import DOMAIN, FsicomixScraper


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class FsicomixEnScraper(FsicomixScraper):
    """FSIComics (apex) chapter and series scraper."""

    domain = DOMAIN
    name = "fsicomics"
    site_id = "fsicomics"
    display_name = "FSIComics"
    chapter_url_pattern = "/{comic-slug}/"
    series_url_pattern = "/all-porn-comics/..."
    version = "2.0.0"
    test_url = "https://fsicomics.com/taming-the-beast-chapter-5-kizaru3d/"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"
    language = "en"

"""FSI Comics IT (it.fsicomics.com) — Italian mirror.

Same catalogue and Foxiz markup as the apex; only the localized title format,
chapter words and path segments differ, all handled by the shared scraper.
"""

from __future__ import annotations

from ...registry import register_scraper
from ._base import FsicomixScraper

DOMAIN = "it.fsicomics.com"


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class FsicomicsItScraper(FsicomixScraper):
    """FSI Comics IT chapter and series scraper."""

    domain = DOMAIN
    name = "fsicomics-it"
    site_id = "fsicomics-it"
    display_name = "FSI Comics IT"
    content_warning = "nsfw"
    chapter_url_pattern = "/{comic-slug}/"
    series_url_pattern = "/{category}/{artist}/"
    version = "2.0.0"
    test_url = "https://it.fsicomics.com/oba-to-haha-zenpen-zia-e-madre-capitolo-2-nishikawa-kou/"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"
    language = "it"
    base = "https://it.fsicomics.com"

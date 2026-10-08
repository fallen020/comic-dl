"""DivaScans (divascans.org) chapter and series scraper."""

from __future__ import annotations

from ..registry import register_scraper
from ._valiscans import ValiScansScraper

DOMAIN = "divascans.org"


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class DivaScansScraper(ValiScansScraper):
    """DivaScans chapter and series scraper."""

    domain = DOMAIN
    name = "divascans"
    site_id = "divascans"
    display_name = "DivaScans"
    chapter_url_pattern = "/series/comic/{slug}/chapter/{n}"
    series_url_pattern = "/series/comic/{slug}/"
    version = "1.0.1"
    test_url = "https://divascans.org/series/comic/obedient-pregnancy/chapter/1"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"

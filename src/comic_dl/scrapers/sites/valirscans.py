"""ValirScans (valirscans.org) chapter and series scraper."""

from __future__ import annotations

from ..registry import register_scraper
from ._valiscans import ValiScansScraper

DOMAIN = "valirscans.org"


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class ValirScansScraper(ValiScansScraper):
    """ValirScans chapter and series scraper."""

    domain = DOMAIN
    name = "valirscans"
    site_id = "valirscans"
    display_name = "ValirScans"
    chapter_url_pattern = "/series/comic/{slug}/chapter/{n}"
    series_url_pattern = "/series/comic/{slug}/"
    version = "1.0.1"
    test_url = "https://valirscans.org/series/comic/not-your-typical-reincarnation-story/chapter/1"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"

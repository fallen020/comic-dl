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
    version = "1.0.0"
    minimum_core_version = "0.0.2"

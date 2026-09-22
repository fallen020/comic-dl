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
    version = "1.0.0"
    minimum_core_version = "0.0.2"

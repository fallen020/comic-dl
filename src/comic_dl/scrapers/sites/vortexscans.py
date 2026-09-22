"""Vortex Scans (vortexscans.org) chapter and series scraper."""

from __future__ import annotations

from ..registry import register_scraper
from ._vcomics import VComicsScraper

DOMAIN = "vortexscans.org"


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class VortexScansScraper(VComicsScraper):
    """Vortex Scans chapter and series scraper."""

    domain = DOMAIN
    name = "vortexscans"
    site_id = "vortexscans"
    version = "1.0.0"
    minimum_core_version = "0.0.2"
    api_base = "https://api.vortexscans.org"
    site_label = "Vortex Scans"

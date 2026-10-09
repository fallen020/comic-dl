"""Nyx Scans (nyxscans.com) chapter and series scraper."""

from __future__ import annotations

from ..registry import register_scraper
from ._vcomics import VComicsScraper

DOMAIN = "nyxscans.com"


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class NyxScansScraper(VComicsScraper):
    """Nyx Scans chapter and series scraper."""

    domain = DOMAIN
    name = "nyxscans"
    site_id = "nyxscans"
    display_name = "Nyx Scans"
    content_warning = "safe"
    chapter_url_pattern = "/series/{slug}/chapter-{n}"
    series_url_pattern = "/series/{slug}/"
    version = "1.0.1"
    test_url = "https://nyxscans.com/series/operation-true-love/chapter-1"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"
    api_base = "https://api.nyxscans.com"
    site_label = "Nyx Scans"

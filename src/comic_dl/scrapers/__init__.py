"""Built-in sources and the single scraper registry.

``comic_dl.scrapers`` is the one home for the source/scraper taxonomy:
``sites/`` holds the per-site implementations (one module per supported
site), the shared framework scrapers (``base``, ``generic``, ``madara``) and
``registry.py`` (registration, plugin discovery, URL resolution) live here,
and ``models.py`` / ``comic_dl.models`` provide the data contracts. Importing
this package registers every built-in source. Third-party plugins register
through the ``comic_dl.sources`` entry-point group (see
``docs/usage/write-plugin.md``).
"""

from . import generic as generic
from . import madara as madara
from . import sites as sites  # importing registers the built-in site scrapers
from .registry import (
    ENTRY_POINT_GROUP,
    SourceEntry,
    get_chapter_scraper,
    get_entry,
    get_generic_scraper,
    get_series_scraper,
    get_series_source_for_url,
    get_source_for_url,
    instances_for,
    list_sources,
    load_plugins,
    register,
    register_builtin,
    register_generic,
    register_scraper,
    url_in_domain,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "SourceEntry",
    "generic",
    "get_chapter_scraper",
    "get_entry",
    "get_generic_scraper",
    "get_series_scraper",
    "get_series_source_for_url",
    "get_source_for_url",
    "instances_for",
    "list_sources",
    "load_plugins",
    "madara",
    "register",
    "register_builtin",
    "register_generic",
    "register_scraper",
    "sites",
    "url_in_domain",
]

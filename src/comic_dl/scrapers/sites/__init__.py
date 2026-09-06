"""Site-specific scraper implementations.

One module per supported site, each decorated with ``@register_scraper``.
Importing this package (which ``comic_dl.scrapers`` does) imports every module
and so wires each site into the shared registry — the domain-keyed lookup in
``comic_dl.scrapers.registry`` only ever sees registered sources. Framework
logic (shared ``base`` helpers, the ``generic`` and ``madara`` scrapers) stays
one level up in ``comic_dl.scrapers``.
"""

from . import asurascans as asurascans
from . import ehentai as ehentai
from . import flamecomics as flamecomics
from . import fsicomics as fsicomics
from . import gedecomix as gedecomix
from . import kagane as kagane
from . import kodokustudio as kodokustudio
from . import mangadex as mangadex
from . import manhwaz as manhwaz
from . import pawchive as pawchive
from . import toonily as toonily
from . import webtoon as webtoon

__all__ = [
    "asurascans",
    "ehentai",
    "flamecomics",
    "fsicomics",
    "gedecomix",
    "kagane",
    "kodokustudio",
    "mangadex",
    "manhwaz",
    "pawchive",
    "toonily",
    "webtoon",
]

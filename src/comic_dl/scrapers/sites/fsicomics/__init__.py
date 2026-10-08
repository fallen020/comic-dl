"""FSI Comics: apex host plus the four language mirrors, one shared scraper.

Each module registers its own host, because the CLI resolves a source by exact
host. ``_base`` is underscore-prefixed so it is not mistaken for a site module
and registers nothing on its own.
"""

from __future__ import annotations

from . import apex as apex
from . import de as de
from . import es as es
from . import fr as fr
from . import it as it
from ._base import FsicomixScraper as FsicomixScraper

__all__ = ["FsicomixScraper", "apex", "de", "es", "fr", "it"]

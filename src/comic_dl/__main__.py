"""Console entry point: installs the sync/async shim and runs the CLI."""

import asyncio
import sys

from .errors import ComicError


def entry() -> int:
    """Synchronous entry point for the ``comic-dl`` console script.

    Importing the CLI registers every built-in site scraper, which can fail
    fast when one of them is broken (see ``comic_dl.scrapers.sites``). The
    import is wrapped so that failure surfaces as the normal user-friendly
    error (with hint and ``-vvv`` traceback) instead of a raw traceback from
    inside the interpreter's import machinery.
    """
    try:
        from .cli import main
    except ComicError as exc:
        from .ui import report_error

        return report_error(exc)
    return asyncio.run(main())


if __name__ == "__main__":
    sys.exit(entry())

"""Console entry point: installs the sync/async shim and runs the CLI."""

import asyncio
import sys

from .cli import main


def entry() -> int:
    """Synchronous entry point for the ``comic-dl`` console script."""
    return asyncio.run(main())


if __name__ == "__main__":
    sys.exit(entry())

"""comic-dl: download comic and manga galleries as CBZ archives."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    from ._version import __version__
except ImportError:
    try:
        __version__ = _pkg_version("comic-dl")
    except PackageNotFoundError:
        __version__ = "0.0.0.dev0"

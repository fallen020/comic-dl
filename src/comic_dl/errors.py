"""Process exit-code constants and the user-facing exception hierarchy."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes (0 ok / 1 error / 2 usage / 130 interrupted).

    ``IntEnum`` rather than ``Enum`` so members pass straight to
    :func:`sys.exit` (``int(ExitCode.USAGE) == 2``).
    """

    OK = 0
    ERROR = 1
    USAGE = 2
    INTERRUPTED = 130


#: Backwards-compatible spellings; prefer the ``ExitCode`` members directly.
EXIT_OK = ExitCode.OK
EXIT_ERROR = ExitCode.ERROR
EXIT_USAGE = ExitCode.USAGE
EXIT_INTERRUPTED = ExitCode.INTERRUPTED


class ComicError(Exception):
    """Base class for user-facing comic-dl errors.

    Carries a user-facing ``message`` plus an optional actionable ``hint``.
    Subclasses never leak raw exception args to the user. ``kind`` is a
    stable machine-readable category so programmatic consumers (JSON mode,
    the library) can branch without parsing message text.
    """

    exit_code = ExitCode.ERROR
    kind = "error"
    _default_message = "Something went wrong."

    def __init__(self, message: str | None = None, *, hint: str = "") -> None:
        self.message = message if message is not None else self._default_message
        self.hint = hint
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


class ValidationError(ComicError):
    """Invalid CLI usage or input (usage-level, exit 2)."""

    exit_code = ExitCode.USAGE
    kind = "usage"
    _default_message = "Invalid input."


class DownloadError(ComicError):
    """A download failed at runtime."""

    kind = "download"
    _default_message = "Download failed."


class ScrapeTimeout(ComicError):
    """A scrape request exceeded the hard timeout.

    Raised in place of the builtin :class:`TimeoutError` so retry loops can
    tell "the server stalled" apart from programming errors, and so the
    user sees a message naming the URL instead of a bare ``TimeoutError``.
    """

    kind = "timeout"
    _default_message = "The request timed out."

    def __init__(self, url: str, timeout: float) -> None:
        self.url = url
        self.timeout = timeout
        super().__init__(f"Request timed out after {timeout:.0f}s fetching {url}")


class ScrapeError(ComicError, ValueError):
    """A page-level scrape failure (no images, auth wall, listing page…).

    Also subclasses :class:`ValueError` because the CLI's per-chapter error
    handling catches ``ValueError``; scrapers raising this get their message
    and ``hint`` surfaced without touching those catch sites. The ``hint``
    is an actionable next step, shown by callers that understand
    :class:`ScrapeError` and ignored as plain text everywhere else.
    """

    kind = "scrape"
    _default_message = "Scraping failed."


class DownloadTimeout(DownloadError):
    """An image download exceeded the hard timeout.

    Carries the local ``filename`` so diagnostics and the user-facing
    message identify exactly which image stalled.
    """

    kind = "timeout"
    _default_message = "A download timed out."

    def __init__(self, filename: str, timeout: float) -> None:
        self.filename = filename
        self.timeout = timeout
        super().__init__(f"Download timed out after {timeout:.0f}s ({filename})")


class LibraryError(ComicError):
    """The library database or a library operation failed."""

    kind = "library"
    _default_message = "Library error."

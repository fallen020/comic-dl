"""``comic-dl self`` — detect the installation source and update through its owner.

Updates never guess. OS-package installs are updated by the owning package
manager from a freshly downloaded package artifact, pip/uv installs by the
tool that created them, source checkouts are left for ``git``, and any
installation mode we cannot identify confidently is reported instead of
acted on.

The outbound fetches reuse the scrapers' shared fetch/stream helpers
(:meth:`BaseScraper._timeout_get`, :func:`downloader._open_stream`), so every
hop is validated against SSRF targets, paced by the per-host rate limiter, and
bounded by the standard timeouts. A lookup failure never touches the download
path — it only affects this command.
"""

from __future__ import annotations

import enum
import json
import os
import platform
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from curl_cffi.requests import AsyncSession
from rich.prompt import Confirm

from . import __version__ as _version
from .downloader import _open_stream
from .errors import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK
from .scrapers.base import BaseScraper
from .ui import print_dim, print_error, print_success, print_warning
from .utils import http_client_args

GITHUB_API_RELEASES_LATEST = (
    "https://api.github.com/repos/fallen020/comic-dl/releases/latest"
)
GITHUB_RELEASES_PAGE = "https://github.com/fallen020/comic-dl/releases"

#: Package artifacts ship per release; extension + arch select the right one.
_DEB_SUFFIX = "_amd64.deb"
_RPM_SUFFIX = ".x86_64.rpm"
_ARCH_SUFFIX = "-x86_64."
_PACMAN_SUFFIX = ".pkg.tar.zst"


class InstallKind(enum.Enum):
    """How comic-dl is installed, and therefore who owns an update."""

    APT = "apt"
    RPM = "rpm"
    PACMAN = "pacman"
    PIP = "pip"
    UV = "uv"
    SOURCE = "source"
    BINARY = "binary"
    UNKNOWN = "unknown"


#: Human labels for each install source, shown in ``self update`` output.
_INSTALL_LABELS = {
    InstallKind.APT: "apt (Debian/Ubuntu)",
    InstallKind.RPM: "rpm (Fedora/RHEL)",
    InstallKind.PACMAN: "pacman (Arch)",
    InstallKind.PIP: "pip environment",
    InstallKind.UV: "uv tool",
    InstallKind.SOURCE: "source checkout",
    InstallKind.BINARY: "standalone executable",
    InstallKind.UNKNOWN: "unknown",
}


def _install_label(kind: InstallKind) -> str:
    return _INSTALL_LABELS.get(kind, kind.value)


@dataclass(frozen=True)
class InstallationInfo:
    """Result of :func:`detect_installation`."""

    kind: InstallKind
    executable: Path
    location: Path | None = None
    writable: bool = False


@dataclass(frozen=True)
class ReleaseInfo:
    """A published GitHub release and its asset map (name -> url)."""

    tag: str
    version: str
    assets: dict[str, str]


def compare_versions(installed: str, latest: str) -> int:
    """Order two version strings (-1 / 0 / 1).

    Tags follow PEP 440 ``vX.Y.Z`` with no prerelease suffixes (enforced by
    the release policy), so an integer-tuple compare is exact today. A local
    prerelease build sorts below the release with the same number, so it
    never reports "up to date" against a published version.
    """
    return (1 if _version_key(installed) > _version_key(latest)
            else -1 if _version_key(installed) < _version_key(latest) else 0)


def parse_version(raw: str) -> tuple[int, ...]:
    """Numeric dot-parts of ``raw`` (trailing prerelease text ignored)."""
    s = raw.strip().lstrip("vV= ")
    out: list[int] = []
    for piece in s.split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def _version_key(raw: str) -> tuple[tuple[int, ...], int]:
    """Sort key: (release parts, 1 for a release, 0 for a prerelease).

    The prerelease flag compares only after the numeric parts match — a dev
    build of ``0.0.2`` therefore sits below the published ``0.0.2`` instead
    of being pinned to it.
    """
    s = raw.strip().lstrip("vV= ")
    prerelease = False
    for piece in s.split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        if len(digits) < len(piece):
            prerelease = True
        if not digits:
            prerelease = True
            break
    return (parse_version(raw), 0 if prerelease else 1)


def detect_installation() -> InstallationInfo:
    """Layered detection: frozen, OS package owner, editable, uv, pip.

    OS-package ownership is authoritative (a lost-package entry in the host
    package manager overrides every other signal). Detection never needs
    privileges: the ``<tool> -S/-Q <path>`` file-ownership queries run as
    the current user and each tool is only invoked when present on PATH.
    """
    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return InstallationInfo(
            InstallKind.BINARY, exe, writable=_writable(exe.parent)
        )

    script = _console_script() or exe
    owner = _os_package_owner(script)
    if owner is not None:
        return InstallationInfo(owner, script)

    dist = _distribution()
    if dist is None:
        return InstallationInfo(InstallKind.UNKNOWN, script)

    location = _dist_location(dist)
    if _is_editable(dist) or _looks_like_checkout(location):
        return InstallationInfo(
            InstallKind.SOURCE,
            script,
            location=location,
            writable=_writable(location) if location else False,
        )

    if _uv_tool_install(exe):
        return InstallationInfo(
            InstallKind.UV, exe, location=location, writable=_writable(exe.parent)
        )

    if sys.prefix != sys.base_prefix:
        return InstallationInfo(
            InstallKind.PIP,
            script,
            location=location,
            writable=_writable(Path(sys.prefix)),
        )
    if _user_site_writable():
        return InstallationInfo(InstallKind.PIP, script, location=location, writable=True)

    # System Python (prefix == base_prefix, not a user install): pip here may
    # need privileges we must not assume. Report rather than guess means the
    # update path instructs instead of risking a half-managed environment.
    return InstallationInfo(
        InstallKind.PIP, script, location=location, writable=False
    )


def select_asset(release: ReleaseInfo, kind: InstallKind) -> tuple[str, str] | None:
    """Pick the package asset URL for ``kind`` on this machine, or ``None``.

    Returns ``(asset_name, url)``. Architecture is matched in the asset name
    since the release ships one package per distro per arch (the machine
    reports ``x86_64`` where Debian names the arch ``amd64``). An arch with no
    published asset (pacman arm64) yields ``None`` so the caller instructs.
    """
    arch_map = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    arch = arch_map.get((platform.machine() or "").lower())
    if arch is None:
        return None
    suffix: str | None = None
    if kind is InstallKind.APT:
        suffix = f"_{arch}.deb"
    elif kind is InstallKind.RPM:
        rpm_arch = "x86_64" if arch == "amd64" else "aarch64"
        suffix = f".{rpm_arch}.rpm"
    elif kind is InstallKind.PACMAN:
        suffix = _PACMAN_SUFFIX if arch == "amd64" else None
    else:
        return None
    if suffix is None:
        return None
    candidates = {
        name: url for name, url in release.assets.items()
        if name.endswith(suffix) and (kind is not InstallKind.PACMAN or _ARCH_SUFFIX in name)
    }
    if not candidates:
        return None
    name = sorted(candidates)[0]
    return name, candidates[name]


def install_args(kind: InstallKind, artifact: Path) -> list[str]:
    """Arg list that installs a downloaded artifact via the owning package
    manager. ``--yes``/``--noconfirm`` keep non-interactive installs moving."""
    if kind is InstallKind.APT:
        return ["apt", "install", "--yes", str(artifact)]
    if kind is InstallKind.RPM:
        return ["dnf", "install", "--yes", str(artifact)]
    if kind is InstallKind.PACMAN:
        return ["pacman", "-U", "--noconfirm", str(artifact)]
    raise ValueError(f"no package-manager install for {kind.name}")


async def fetch_latest_release() -> ReleaseInfo | None:
    """The latest published release, or ``None`` on any lookup failure.

    Offline, API-rate-limited, and malformed responses collapse to ``None`` —
    the caller turns that into a user-facing "unable to check" message without
    leaking exception details.
    """
    try:
        async with AsyncSession(**http_client_args(host="api.github.com")) as client:
            resp = await BaseScraper._timeout_get(
                GITHUB_API_RELEASES_LATEST, client, use_cache=True
            )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    tag = str(data.get("tag_name") or "")
    if not tag:
        return None
    assets: dict[str, str] = {}
    for item in data.get("assets") or []:
        name = (item or {}).get("name")
        url = (item or {}).get("browser_download_url")
        if name and url:
            assets[str(name)] = str(url)
    # Tags are ``vX.Y.Z``; display and compare the bare ``X.Y.Z`` form so
    # output never mixes prefix styles.
    return ReleaseInfo(tag=tag, version=tag.lstrip("vV"), assets=assets)


async def download_artifact(url: str, dest: Path) -> None:
    """Stream a release asset to ``dest`` with the shared validated streamer."""
    async with AsyncSession(**http_client_args()) as client:
        resp = await _open_stream(client, url)
        try:
            if resp.status_code >= 400:
                raise OSError(f"HTTP {resp.status_code}")
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_content(1 << 16):
                    fh.write(chunk)
        finally:
            await resp.aclose()


async def run_update_command(*, check: bool, yes: bool) -> int:
    """``comic-dl self update``: check, then update through the owner.

    Returns an exit code. ``check=True`` never modifies anything. Confirmations
    are skipped with ``yes``; a non-interactive run without ``--yes`` refuses
    (mirrors ``cookie clear``), and every failure falls back to exact manual
    instructions.
    """
    info = detect_installation()
    print_dim(f"Installed via:        {_install_label(info.kind)}")
    print_dim(f"Installed version:    {_version}")

    release = await fetch_latest_release()
    if release is None:
        print_dim("Latest version:       unknown")
        print_error("Unable to check for updates: network or GitHub API unavailable.")
        print_dim(f"Latest releases: {GITHUB_RELEASES_PAGE}")
        return EXIT_ERROR

    latest = release.version
    print_dim(f"Latest version:       {latest}")
    if compare_versions(_version, latest) >= 0:
        print_success("Status:              already up to date.")
        return EXIT_OK
    print_warning(f"Status:              update available ({_version} → {latest}).")

    if check:
        return EXIT_OK

    if info.kind is InstallKind.SOURCE:
        print_dim("comic-dl is running from a source checkout.")
        print_dim("Update it with:")
        print_dim("  git pull")
        print_dim("  uv sync")
        return EXIT_OK
    if info.kind is InstallKind.BINARY:
        print_dim(f"Download the new release: {GITHUB_RELEASES_PAGE}")
        return EXIT_OK
    if info.kind is InstallKind.UNKNOWN:
        print_error("Could not identify how comic-dl was installed.")
        print_dim(f"Reinstall from: {GITHUB_RELEASES_PAGE}")
        return EXIT_ERROR
    if info.kind is InstallKind.PIP:
        return _update_pip(info, latest, yes)
    if info.kind is InstallKind.UV:
        return _update_uv(latest, yes)

    blocked = _request_confirmation(
        yes,
        f"Install comic-dl {latest} via {info.kind.value} (requires sudo)?",
        says_interactive_no="Update skipped.",
    )
    if blocked is not None:
        return blocked
    return await _install_package(info, release, latest)


def _update_pip(info: InstallationInfo, latest: str, yes: bool) -> int:
    if not info.writable:
        print_error("This environment is not writable.")
        print_dim(
            "Reinstall in a virtual environment, or run with the tool that "
            f"manages it:  {sys.executable} -m pip install --upgrade comic-dl"
        )
        return EXIT_ERROR
    blocked = _request_confirmation(
        yes,
        f"Update comic-dl {_version} → {latest} with pip?",
        says_interactive_no="Update skipped.",
    )
    if blocked is not None:
        return blocked
    argv = [sys.executable, "-m", "pip", "install", "--upgrade", "comic-dl"]
    rc = subprocess.run(argv).returncode  # nosec B603
    if rc == 0:
        print_success(f"Updated to comic-dl {latest}.")
        return EXIT_OK
    print_error(f"pip upgrade failed (exit code {rc}).")
    print_dim(f"Run it yourself: {' '.join(argv)}")
    return EXIT_ERROR


def _update_uv(latest: str, yes: bool) -> int:
    blocked = _request_confirmation(
        yes,
        f"Update comic-dl {_version} → {latest} with uv?",
        says_interactive_no="Update skipped.",
    )
    if blocked is not None:
        return blocked
    argv = ["uv", "tool", "upgrade", "comic-dl"]
    rc = subprocess.run(argv).returncode  # nosec B603
    if rc == 0:
        print_success(f"Updated to comic-dl {latest}.")
        return EXIT_OK
    print_error(f"uv upgrade failed (exit code {rc}).")
    print_dim(f"Run it yourself: {' '.join(argv)}")
    return EXIT_ERROR


async def _install_package(
    info: InstallationInfo, release: ReleaseInfo, latest: str
) -> int:
    """Download the matching package artifact and hand it to a privileged
    install; every failure instructs instead of leaving the system touched."""
    asset = select_asset(release, info.kind)
    if asset is None:
        arch = (platform.machine() or "").lower()
        print_error(f"No {info.kind.value} package is published for {arch}.")
        print_dim(f"Pick a matching asset from: {GITHUB_RELEASES_PAGE}")
        return EXIT_ERROR

    name, url = asset
    tmp_root = Path(tempfile.mkdtemp(prefix="comic-dl-self-"))
    artifact = tmp_root / name
    print_dim(f"Downloading {name} ...")
    try:
        await download_artifact(url, artifact)
    except Exception:
        print_error(f"Failed to download {name}.")
        print_dim(f"Latest releases: {GITHUB_RELEASES_PAGE}")
        _cleanup(tmp_root)
        return EXIT_ERROR

    is_root = _is_root()
    if is_root:
        argv = install_args(info.kind, artifact)
    else:
        sudo = shutil.which("sudo")
        if sudo is None:
            print_error("sudo is required to install the package.")
            print_dim(f"Install manually from: {GITHUB_RELEASES_PAGE}")
            _cleanup(tmp_root)
            return EXIT_ERROR
        argv = [sudo, *install_args(info.kind, artifact)]

    print_dim(f"Running: {' '.join(argv)}")
    rc = subprocess.run(argv).returncode  # nosec B603
    _cleanup(tmp_root)
    if rc == 0:
        print_success(f"Installed comic-dl {latest}.")
        return EXIT_OK
    print_error(f"The {info.kind.value} install failed (exit code {rc}).")
    print_dim(f"Run it yourself: {' '.join(argv)}")
    return EXIT_ERROR


def _request_confirmation(
    yes: bool, question: str, *, says_interactive_no: str
) -> int | None:
    """Exit code to return because confirmation was absent/refused, else None.

    ``yes`` skips the prompt. A non-interactive run without ``--yes`` refuses
    outright (the ``cookie clear`` convention: never silently proceed in an
    unattended prompt), while an interactive ``no`` returns a plain skip.
    """
    if yes:
        return None
    if not _is_interactive():
        print_error("Updating requires confirmation.")
        print_dim("Re-run with -y/--yes to proceed without a prompt.")
        return EXIT_INTERRUPTED
    if not Confirm.ask(question, default=False):
        print_dim(says_interactive_no)
        return EXIT_OK
    return None


def _console_script() -> Path | None:
    """Resolved path of the ``comic-dl`` script on PATH, if any."""
    found = shutil.which("comic-dl")
    if not found:
        return None
    try:
        return Path(found).resolve()
    except OSError:
        return None


def _os_package_owner(script: Path) -> InstallKind | None:
    """The host package manager owning ``script``, or ``None``.

    Positive file-ownership answers from ``dpkg``/``rpm``/``pacman`` are
    authoritative for the OS-managed install row in the matrix.
    """
    if os.name == "nt":
        return None
    probes = (
        ("dpkg-query", InstallKind.APT),
        ("rpm", InstallKind.RPM),
        ("pacman", InstallKind.PACMAN),
    )
    for tool, kind in probes:
        path = shutil.which(tool)
        if path is None or not script.exists():
            continue
        flag = "-S" if tool == "dpkg-query" else ("-qf" if tool == "rpm" else "-Qo")
        try:
            rc = subprocess.run(  # nosec B603
                [path, flag, str(script)],
                capture_output=True,
            ).returncode
        except OSError:
            continue
        if rc == 0:
            return kind
    return None


def _distribution() -> Any | None:
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        return distribution("comic-dl")
    except PackageNotFoundError:
        return None


def _dist_location(dist: Any) -> Path | None:
    try:
        return Path(dist.locate())
    except Exception:
        return None


def _is_editable(dist: Any) -> bool:
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        return False
    try:
        return json.loads(raw).get("dir_info", {}).get("editable", False)
    except (ValueError, TypeError, AttributeError):
        return False


def _looks_like_checkout(location: Path | None) -> bool:
    """True when ``location`` sits inside the project checkout (belt for
    editable installs whose direct_url metadata is absent)."""
    if location is None:
        return False
    current = location
    for _ in range(6):
        if (current / ".git").exists() and (current / "pyproject.toml").exists():
            return True
        if current.parent == current:
            break
        current = current.parent
    return False


def _uv_tool_install(exe: Path) -> bool:
    tools = os.environ.get("UV_TOOL_DIR")
    root = Path(tools).expanduser() if tools else Path.home() / ".local/share/uv/tools"
    try:
        exe.relative_to(root)
        return True
    except ValueError:
        return False


def _user_site_writable() -> bool:
    import site as _site

    return _writable(Path(_site.getusersitepackages()))


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _is_interactive() -> bool:
    from .ui import is_interactive

    return is_interactive()


def _cleanup(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)

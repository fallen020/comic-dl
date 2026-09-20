"""Offline tests for ``comic-dl self`` (version + installation-aware update).

No real network, package manager, pip, uv, sudo, or download is ever
involved: installs are faked with monkeypatched fixtures and subprocesses are
mocked, matching the repo-wide offline test convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from comic_dl import __version__ as _CORE_VERSION
from comic_dl.errors import EXIT_USAGE
from comic_dl.self_update import (
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    InstallationInfo,
    InstallKind,
    ReleaseInfo,
    _request_confirmation,
    compare_versions,
    detect_installation,
    install_args,
    parse_version,
    run_update_command,
    select_asset,
)

# Functional install fixture location; never touches disk.
_MOCK_EXE = Path("/mock/comic-dl")

# Update-path fixtures need a release strictly newer than what's installed;
# deriving it keeps the tests independent of the current version number.


def _bump_patch(v: str) -> str:
    parts = [int(p) for p in v.split(".")]
    parts[-1] += 1
    return ".".join(str(p) for p in parts)


_FUTURE = _bump_patch(_CORE_VERSION)
_FUTURE_TAG = f"v{_FUTURE}"


def _rel(tag: str, *assets: str) -> ReleaseInfo:
    return ReleaseInfo(
        tag=tag,
        version=tag,
        assets={name: f"https://example.invalid/{name}" for name in assets},
    )


def _text(capsys) -> str:
    """Console + diagnostic output without Rich's folding newlines."""
    cap = capsys.readouterr()
    return (cap.out + cap.err).replace("\n", "")


class TestVersionParsing:
    def test_plain(self):
        assert parse_version("0.0.2") == (0, 0, 2)

    def test_v_prefix_and_equals(self):
        assert parse_version("v0.0.2") == (0, 0, 2)
        assert parse_version("=0.0.2") == (0, 0, 2)

    def test_prerelease_keeps_release_number(self):
        assert parse_version("0.0.2.dev1") == (0, 0, 2)

    def test_partial_drop(self):
        assert parse_version("0.1") == (0, 1)


class TestVersionCompare:
    def test_equal_with_v_prefix(self):
        assert compare_versions("0.0.2", "v0.0.2") == 0

    def test_newer(self):
        assert compare_versions("0.0.2", "v0.0.3") == -1
        assert compare_versions("0.0.3", "v0.0.2") == 1

    def test_minor_ordering(self):
        assert compare_versions("0.1.0", "0.0.9") == 1

    def test_dev_snapshot_never_up_to_date(self):
        assert compare_versions("0.0.2.dev1", "v0.0.2") == -1


class TestSelectAsset:
    def test_apt_amd64(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "x86_64")
        rel = _rel("v0.0.3", "comic-dl_0.0.3_amd64.deb", "other.txt")
        assert select_asset(rel, InstallKind.APT) == (
            "comic-dl_0.0.3_amd64.deb",
            "https://example.invalid/comic-dl_0.0.3_amd64.deb",
        )

    def test_rpm_x86_64(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "x86_64")
        rel = _rel("v0.0.3", "comic-dl-0.0.3-1.x86_64.rpm")
        assert select_asset(rel, InstallKind.RPM) == (
            "comic-dl-0.0.3-1.x86_64.rpm",
            "https://example.invalid/comic-dl-0.0.3-1.x86_64.rpm",
        )

    def test_pacman_x86_64(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "x86_64")
        rel = _rel("v0.0.3", "comic-dl-0.0.3-1-x86_64.pkg.tar.zst")
        assert select_asset(rel, InstallKind.PACMAN) == (
            "comic-dl-0.0.3-1-x86_64.pkg.tar.zst",
            "https://example.invalid/comic-dl-0.0.3-1-x86_64.pkg.tar.zst",
        )

    def test_pacman_arm64_none(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "arm64")
        rel = _rel("v0.0.3", "comic-dl-0.0.3-1-x86_64.pkg.tar.zst")
        assert select_asset(rel, InstallKind.PACMAN) is None

    def test_apt_ignores_other_arch(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "x86_64")
        rel = _rel("v0.0.3", "comic-dl_0.0.3_arm64.deb")
        assert select_asset(rel, InstallKind.APT) is None

    def test_unknown_kind(self):
        rel = _rel("v0.0.3", "comic-dl_0.0.3_amd64.deb")
        assert select_asset(rel, InstallKind.SOURCE) is None


class TestInstallArgs:
    def test_apt_command(self):
        argv = install_args(InstallKind.APT, Path("/x/comic.deb"))
        assert argv[0] == "apt"

    def test_pacman_command(self):
        argv = install_args(InstallKind.PACMAN, Path("/x/c.pkg.tar.zst"))
        assert argv[0] == "pacman" and "-U" in argv


class TestSelfVersion:
    async def test_self_version_prints(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["version"])
        assert rc == EXIT_OK
        assert f"comic-dl {_CORE_VERSION}" in _text(capsys)

    async def test_bare_self_is_usage_error(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "self"])
        from comic_dl.cli import main

        assert await main() == EXIT_USAGE

    async def test_deprecated_version_alias_still_works(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["prog", "--version"])
        from comic_dl.cli import main

        with pytest.raises(SystemExit) as exc:
            await main()
        assert exc.value.code == EXIT_OK
        assert f"comic-dl {_CORE_VERSION}" in _text(capsys)


class TestSelfErrors:
    """Friendly failure text for the self command group."""

    async def test_missing_command(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self([])
        assert rc == EXIT_USAGE
        out = _text(capsys)
        assert "missing command" in out
        assert "comic-dl self --help" in out

    async def test_version_option_hint(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["--version"])
        assert rc == EXIT_USAGE
        assert "comic-dl self version" in _text(capsys)

    async def test_update_option_hint(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["--update"])
        assert rc == EXIT_USAGE
        assert "comic-dl self update" in _text(capsys)

    async def test_command_fuzzy_suggestion(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["updat"])
        assert rc == EXIT_USAGE
        out = _text(capsys)
        assert "Did you mean: update?" in out
        assert "Commands: version" not in out

    async def test_option_fuzzy_suggestion(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["--hep"])
        assert rc == EXIT_USAGE
        assert "Did you mean: --help?" in _text(capsys)

    async def test_unknown_without_suggestion(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["deploy"])
        assert rc == EXIT_USAGE
        assert "unknown command 'deploy'" in _text(capsys)

    async def test_help_option_shows_group_help(self, capsys):
        from comic_dl.cli import _run_self

        assert await _run_self(["--help"]) == EXIT_OK
        assert "comic-dl self <COMMAND>" in _text(capsys)


class TestUpdateCheck:
    async def test_network_failure(self, monkeypatch, capsys):
        async def boom():
            return None

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", boom)
        rc = await run_update_command(check=True, yes=True)
        assert rc == EXIT_ERROR
        assert "Unable to check for updates" in _text(capsys)

    async def test_already_up_to_date(self, monkeypatch, capsys):
        async def current():
            return _rel("v0.0.2")

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", current)
        rc = await run_update_command(check=True, yes=True)
        assert rc == EXIT_OK
        out = _text(capsys)
        assert "already up to date" in out
        assert "Installed via:" in out

    async def test_update_available_check_only(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PIP, _MOCK_EXE, writable=True),
        )
        rc = await run_update_command(check=True, yes=True)
        assert rc == EXIT_OK
        out = _text(capsys)
        assert "update available" in out
        assert "Installed version:" in out and "Latest version:" in out


class TestUpdateRefusal:
    async def test_source_checkout(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.SOURCE, _MOCK_EXE),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        out = _text(capsys)
        assert "git pull" in out and "uv sync" in out

    async def test_unknown_installation(self, monkeypatch):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.UNKNOWN, _MOCK_EXE),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR

    async def test_binary_reports_only(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.BINARY, _MOCK_EXE),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        assert "github.com/fallen020/comic-dl" in _text(capsys)


class TestPipUpdate:
    async def test_not_writable(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PIP, _MOCK_EXE, writable=False),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR
        assert "not writable" in _text(capsys)

    async def test_yes_runs_pip(self, monkeypatch):
        import sys as _sys

        async def newer():
            return _rel(_FUTURE_TAG)

        calls: list[list[str]] = []

        def fake_subprocess(argv, **_kw):
            calls.append(list(argv))
            return type("P", (), {"returncode": 0})()

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PIP, _MOCK_EXE, writable=True),
        )
        monkeypatch.setattr("comic_dl.self_update.subprocess.run", fake_subprocess)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        assert calls and calls[0][:4] == [_sys.executable, "-m", "pip", "install"]

    async def test_pip_failure(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PIP, _MOCK_EXE, writable=True),
        )
        monkeypatch.setattr(
            "comic_dl.self_update.subprocess.run",
            lambda *a, **k: type("P", (), {"returncode": 1})(),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR
        assert "pip upgrade failed" in _text(capsys)


class TestUvUpdate:
    async def test_uv_command(self, monkeypatch):
        async def newer():
            return _rel(_FUTURE_TAG)

        calls: list[list[str]] = []

        def fake_subprocess(argv, **_kw):
            calls.append(list(argv))
            return type("P", (), {"returncode": 0})()

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.UV, _MOCK_EXE, writable=True),
        )
        monkeypatch.setattr("comic_dl.self_update.subprocess.run", fake_subprocess)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        assert calls and calls[0] == ["uv", "tool", "upgrade", "comic-dl"]


class TestPackageManagerUpdate:
    async def test_sudo_apt_install(self, monkeypatch):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        async def fake_download(url, dest):
            dest.write_bytes(b"pkg")

        calls: list[list[str]] = []

        def fake_subprocess(argv, **_kw):
            calls.append(list(argv))
            return type("P", (), {"returncode": 0})()

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.APT, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "amd64")
        monkeypatch.setattr("comic_dl.self_update.download_artifact", fake_download)
        monkeypatch.setattr("comic_dl.self_update._request_confirmation", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.self_update._is_root", lambda: False)
        monkeypatch.setattr(
            "comic_dl.self_update.shutil.which",
            lambda name: "/usr/bin/sudo" if name == "sudo" else None,
        )
        monkeypatch.setattr("comic_dl.self_update.subprocess.run", fake_subprocess)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        assert len(calls) == 1
        assert calls[0][0] == "/usr/bin/sudo" and calls[0][1] == "apt"

    async def test_sudo_absent_instructs(self, monkeypatch):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        async def fake_download(url, dest):
            dest.write_bytes(b"pkg")

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.APT, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "amd64")
        monkeypatch.setattr("comic_dl.self_update.download_artifact", fake_download)
        monkeypatch.setattr("comic_dl.self_update._request_confirmation", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.self_update._is_root", lambda: False)
        monkeypatch.setattr("comic_dl.self_update.shutil.which", lambda name: None)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR

    async def test_package_install_failure_instructs(self, monkeypatch):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        async def fake_download(url, dest):
            dest.write_bytes(b"pkg")

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.APT, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "amd64")
        monkeypatch.setattr("comic_dl.self_update.download_artifact", fake_download)
        monkeypatch.setattr("comic_dl.self_update._request_confirmation", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.self_update._is_root", lambda: False)
        monkeypatch.setattr(
            "comic_dl.self_update.shutil.which",
            lambda name: "/usr/bin/sudo" if name == "sudo" else None,
        )
        monkeypatch.setattr(
            "comic_dl.self_update.subprocess.run",
            lambda argv, **_kw: type("P", (), {"returncode": 100})(),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR

    async def test_no_asset_for_arch(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PACMAN, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "arm64")
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR
        assert "No pacman package" in _text(capsys)


class TestConfirmation:
    def test_yes_skips(self):
        assert _request_confirmation(True, "?", says_interactive_no="skipped") is None

    def test_noninteractive_refuses(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update._is_interactive", lambda: False)
        assert _request_confirmation(False, "?", says_interactive_no="skipped") == EXIT_INTERRUPTED

    def test_interactive_no_skips(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update._is_interactive", lambda: True)
        monkeypatch.setattr("comic_dl.self_update.Confirm.ask", lambda *a, **k: False)
        assert _request_confirmation(False, "?", says_interactive_no="skipped") == EXIT_OK

    def test_interactive_yes(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update._is_interactive", lambda: True)
        monkeypatch.setattr("comic_dl.self_update.Confirm.ask", lambda *a, **k: True)
        assert _request_confirmation(False, "?", says_interactive_no="skipped") is None


class TestDetection:
    def test_frozen_is_binary(self, monkeypatch, tmp_path):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", True, raising=False)
        info = detect_installation()
        assert info.kind is InstallKind.BINARY

    def test_os_owner_authoritative(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr(
            "comic_dl.self_update._os_package_owner", lambda script: InstallKind.PACMAN
        )
        info = detect_installation()
        assert info.kind is InstallKind.PACMAN

    def test_unknown_without_distribution(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr("comic_dl.self_update._distribution", lambda: None)
        info = detect_installation()
        assert info.kind is InstallKind.UNKNOWN

    def test_editable_is_source(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr("comic_dl.self_update._distribution", lambda: _FakeDist(editable=True))
        monkeypatch.setattr("comic_dl.self_update._os_package_owner", lambda s: None)
        info = detect_installation()
        assert info.kind is InstallKind.SOURCE

    def test_venv_is_pip(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr("comic_dl.self_update.sys.prefix", "/venv/prefix")
        monkeypatch.setattr("comic_dl.self_update.sys.base_prefix", "/usr")
        monkeypatch.setattr("comic_dl.self_update._distribution", lambda: _FakeDist(editable=False))
        monkeypatch.setattr("comic_dl.self_update._os_package_owner", lambda s: None)
        monkeypatch.setattr("comic_dl.self_update._writable", lambda p: True)
        info = detect_installation()
        assert info.kind is InstallKind.PIP
        assert info.writable is True

    def test_uv_tool(self, monkeypatch, tmp_path):
        tools = tmp_path / "tools"
        exe = tools / "comic-dl" / ".venv" / "bin" / "python"
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr("comic_dl.self_update.sys.executable", str(exe))
        monkeypatch.setattr("comic_dl.self_update.sys.prefix", "/x")
        monkeypatch.setattr("comic_dl.self_update.sys.base_prefix", "/usr")
        monkeypatch.setattr("comic_dl.self_update._distribution", lambda: _FakeDist(editable=False))
        monkeypatch.setattr("comic_dl.self_update._os_package_owner", lambda s: None)
        monkeypatch.setattr("os.environ", {"UV_TOOL_DIR": str(tools)})
        info = detect_installation()
        assert info.kind is InstallKind.UV


class _FakeDist:
    """Minimal stand-in for ``importlib.metadata.Distribution``."""

    def __init__(self, editable: bool):
        self._editable = editable

    def locate(self):
        return Path("/site/comic_dl")

    def read_text(self, filename: str):
        if filename == "direct_url.json":
            return '{"dir_info": {"editable": true}}' if self._editable else '{"dir_info": {}}'
        raise FileNotFoundError(filename)

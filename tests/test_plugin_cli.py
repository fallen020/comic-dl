from __future__ import annotations

import pytest

from comic_dl.cli.plugins import run_plugin_command
from comic_dl.scrapers import registry

GOOD_SOURCE = '''\
from __future__ import annotations


class ExampleSource:
    domain = "example.org"
    name = "example"
    version = "1.2.0"
    capabilities = {"chapter", "series"}
    priority = 0

    def matches_url(self, url: str) -> bool:
        return url.startswith("https://example.org/")

    def matches_series_url(self, url: str) -> bool:
        return "/series/" in url

    async def scrape(self, url, client):
        return None

    async def scrape_series(self, url, client):
        return None
'''

CLASS_BROKEN_SOURCE = '''\
class BrokenSource:
    domain = 42
    capabilities = {"chapter", "nope"}
    priority = "high"

    def matches_url(self, url: str) -> bool:
        return True
'''


@pytest.fixture
def clean_registry():
    """Isolate from plugin state registered by other test files."""
    snapshot = dict(registry._sourcemap)
    registry._sourcemap.clear()
    registry._loaded_plugins.clear()
    errors = registry._plugin_load_errors
    errors.clear()
    yield
    registry._sourcemap.clear()
    registry._sourcemap.update(snapshot)
    registry._loaded_plugins.clear()
    errors.clear()


class TestList:
    def test_empty_library_message(self, clean_registry, capsys):
        assert run_plugin_command("list", []) == 0
        err = capsys.readouterr().err
        assert "No third-party sources installed" in err

    def test_json_empty(self, clean_registry, capsys):
        assert run_plugin_command("list", ["--json"]) == 0
        out = capsys.readouterr().out
        assert '"plugins": []' in out


class TestValidate:
    def test_valid_source(self, tmp_path, capsys):
        f = tmp_path / "source.py"
        f.write_text(GOOD_SOURCE)
        assert run_plugin_command("validate", [str(f)]) == 0
        assert "ExampleSource" in capsys.readouterr().out

    def test_broken_source(self, tmp_path, capsys):
        f = tmp_path / "source.py"
        f.write_text(CLASS_BROKEN_SOURCE)
        assert run_plugin_command("validate", [str(f)]) == 1
        err = capsys.readouterr().err
        assert "BrokenSource" in err
        assert "domain" in err
        assert "capabilities" in err
        assert "priority" in err

    def test_missing_file(self, tmp_path, capsys):
        assert run_plugin_command("validate", [str(tmp_path / "nope.py")]) == 2
        assert "Not a source file" in capsys.readouterr().err

    def test_import_error(self, tmp_path, capsys):
        f = tmp_path / "source.py"
        f.write_text("raise RuntimeError('boom')\n")
        assert run_plugin_command("validate", [str(f)]) == 1
        assert "boom" in capsys.readouterr().err

    def test_directory_with_source_py(self, tmp_path, capsys):
        d = tmp_path / "plugin"
        d.mkdir()
        (d / "source.py").write_text(GOOD_SOURCE)
        assert run_plugin_command("validate", [str(d)]) == 0


class TestScaffold:
    def test_scaffolds_package(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert run_plugin_command("scaffold", ["mysite", "--domain", "mysite.cc"]) == 0
        root = tmp_path / "comic_dl_mysite"
        assert (root / "pyproject.toml").is_file()
        src = root / "comic_dl_mysite" / "source.py"
        assert src.is_file()
        pyproject = (root / "pyproject.toml").read_text()
        assert 'mysite = "comic_dl_mysite.source:MysiteSource"' in pyproject
        assert "mysite.cc" in src.read_text()
        assert "MysiteSource" in src.read_text()

    def test_scaffold_existing_dir(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "comic_dl_mysite").mkdir()
        assert run_plugin_command("scaffold", ["mysite"]) == 2
        assert "already exists" in capsys.readouterr().err

    def test_scaffold_bad_name(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert run_plugin_command("scaffold", ["my site"]) == 2
        assert "Invalid plugin name" in capsys.readouterr().err

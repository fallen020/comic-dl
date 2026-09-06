from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

import comic_dl
from comic_dl import _version

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
VERSION_FILE = Path(_version.__file__)

# Load the local packaging/versioning.py directly: the venv also provides the
# *unrelated* `packaging` PyPI package, which would shadow this directory.
_VERSIONING_FILE = REPO_ROOT / "packaging" / "versioning.py"


def _load_versioning():
    spec = importlib.util.spec_from_file_location("test_versioning", _VERSIONING_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


versioning = _load_versioning()


class TestVersionFileContract:
    def test_committed_file_matches_pyproject(self):
        with PYPROJECT.open("rb") as fh:
            declared = tomllib.load(fh)["project"]["version"]
        assert _version.__version__ == declared
        assert versioning.check_version_file(PYPROJECT, VERSION_FILE) is None

    def test_comic_dl_exports_same_version(self):
        assert comic_dl.__version__ == _version.__version__

    def test_version_tuple_matches(self):
        assert _version.__version_tuple__ == tuple(
            int(part.split("rc")[0].split("b")[0].split("a")[0])
            for part in _version.__version__.split(".")
        )


class TestVersioningWriter:
    def test_render_and_roundtrip(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "1.4.0"\n', encoding="utf-8")
        out = tmp_path / "gen" / "_version.py"
        written = versioning.write_version_file(pyproject, out)
        assert written == "1.4.0"
        assert out.exists()
        assert versioning.check_version_file(pyproject, out) is None

    def test_render_is_deterministic(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "2.0.1"\n', encoding="utf-8")
        a = tmp_path / "_a.py"
        b = tmp_path / "_b.py"
        versioning.write_version_file(pyproject, a)
        versioning.write_version_file(pyproject, b)
        assert a.read_bytes() == b.read_bytes()

    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("0.1.0", (0, 1, 0)),
            ("1.4.0", (1, 4, 0)),
            ("2.0.1", (2, 0, 1)),
            ("1.0.0rc1", (1, 0, 0)),
        ],
    )
    def test_version_tuple_member_parse(self, version, expected):
        text = versioning.render_version_file(version)
        assert f"__version_tuple__ = {expected}" in text

    def test_check_reports_stale_file(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "3.0.0"\n', encoding="utf-8")
        out = tmp_path / "_version.py"
        out.write_text('__version__ = "0.0.0.dev0"\n', encoding="utf-8")
        problem = versioning.check_version_file(pyproject, out)
        assert problem is not None
        assert "3.0.0" in problem

    def test_check_reports_missing_file(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "3.0.0"\n', encoding="utf-8")
        assert versioning.check_version_file(pyproject, tmp_path / "nope.py") is not None

    def test_generated_file_imports_cleanly(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "5.0.0"\n', encoding="utf-8")
        out = tmp_path / "_importme.py"
        versioning.write_version_file(pyproject, out)
        import importlib.util

        spec = importlib.util.spec_from_file_location("_importme", out)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.__version__ == "5.0.0"

# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for building a single-file `comic-dl` executable.
#
# Build (platform-native; PyInstaller cannot cross-compile):
#   pyinstaller --clean --noconfirm packaging/comic-dl.spec
#
# curl_cffi ships prebuilt libcurl-impersonate native libraries that are loaded
# at runtime, so they are collected explicitly. lxml is handled by PyInstaller's
# built-in hook. Submodules are listed as hidden imports so PyInstaller's static
# analysis keeps them even though they are dispatched dynamically.
#
# The binary redistributes third-party code (curl_cffi, lxml, ...), so the
# license texts are bundled under third_party_licenses/ to satisfy their
# copyright/permission notices. Expected at _MEIPASS/third_party_licenses/ in
# one-file mode.
import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# collect_submodules() imports the package to enumerate it, using the live
# sys.path — `pathex` below only affects Analysis. Without this, the site
# package is unimportable at spec-build time and the scan silently returns []
# (a binary that drops every scraper). Site modules are then dispatched
# dynamically by comic_dl.scrapers.sites, so they must all be listed.
sys.path.insert(0, os.path.join(ROOT, "src"))

# Native libcurl-impersonate shared objects that curl_cffi loads dynamically.
curl_libs = collect_dynamic_libs("curl_cffi")

a = Analysis(
    [os.path.join(ROOT, "packaging", "pyinstaller_entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=curl_libs,
    datas=[
        (os.path.join(ROOT, "Third-Party-Licenses"), "third_party_licenses"),
        # Runtime data files read via Path(__file__) (see ui.BANNER_PATH).
        (os.path.join(ROOT, "src", "comic_dl", "banner.txt"), "comic_dl"),
    ],
    hiddenimports=[
        "comic_dl.config",
        "comic_dl.library",
        "comic_dl.cli.library",
        "comic_dl.scrapers.registry",
        "comic_dl.scrapers.sites",
        # Every site module is imported dynamically by the auto-discovering
        # sites package, so collect them wholesale instead of maintaining a
        # per-site list that silently goes stale (the hivetoons-shaped bug).
        "comic_dl.scrapers.generic",
        "comic_dl.scrapers.madara",
        "comic_dl.scrapers.refresh",
        *collect_submodules("comic_dl.scrapers.sites"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="comic-dl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    argv_emulation=False,
)
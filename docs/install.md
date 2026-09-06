# Installation

Two installation methods are supported:

- **Prebuilt binaries** attached to each
  [GitHub Release](https://github.com/fallen020/comic-dl/releases) — no Python
  required
- **From source** with `git clone` + `uv sync` — requires Python 3.11+ and
  [uv](https://docs.astral.sh/uv/getting-started/installation/)

A PyPI release is planned; `pip install comic-dl` is not available until the
first version is published there.

## Prebuilt binaries

Standalone binaries are attached to each
[GitHub Release](https://github.com/fallen020/comic-dl/releases). No Python
required.

### Windows

Download `comic-dl-<ver>-windows-amd64.zip` from the release, unzip it, and run
`comic-dl.exe` from a terminal.

```powershell
.\comic-dl.exe --version
```

Use **Windows Terminal** — the CLI output uses Unicode glyphs. In classic
`cmd.exe`, run `chcp 65001` first.

!!! note "Windows on ARM64"
    There is no native ARM64 Windows build yet (release CI lists it as a
    disabled extension point). The amd64 binary runs via emulation on ARM64
    Windows; for native speed build from source instead.

### macOS

Not distributed as a binary yet — macOS support is planned behind code signing
and notarization (see [the release runbook](./develop/releasing.md)). Build
from source on macOS today.

### Android

No Android package is provided. comic-dl is pure Python; on Android you can run
it inside [Termux](https://termux.dev) by building from source, or create
`.cbz` archives on a desktop machine and copy them to your phone.

### Linux

Each release attaches one package per distro per architecture, named
`comic-dl-<ver>...-<arch>`. Pick the artifact matching your machine:

| Your machine | `uname -m` | Debian/Ubuntu | Fedora/RHEL | Arch |
| :----------- | :--------- | :------------ | :---------- | :--- |
| amd64 / x86_64 | `x86_64` | `_amd64.deb` | `.fcNN.x86_64.rpm` | `-x86_64.pkg.tar.zst` |
| arm64 / aarch64 | `aarch64` | `_arm64.deb` | `.fcNN.aarch64.rpm` | — |

The Fedora RPM name includes the Fedora release it was built against (for
example `-1.fc44.`); that marker is part of the filename, not optional.

Every release artifact is built in CI, installed into a **clean container**
(Debian/Fedora/Arch) or runner (Windows) with no source tree, and smoke-tested
(`--version`, `--list-sources`, `config path`, `help`) before it is attached —
a version that cannot install or report its correct version is never published.

Arch packages are built for amd64 only (`archlinux:latest` no longer ships an
arm64 manifest; use the amd64 package under emulation or build from source on
ARM64 machines).

=== "Debian / Ubuntu"

    ```bash
    curl -LO https://github.com/fallen020/comic-dl/releases/latest/download/comic-dl_0.0.1_amd64.deb
    sudo apt install ./comic-dl_0.0.1_amd64.deb
    ```

=== "Fedora / RHEL"

    ```bash
    curl -LO https://github.com/fallen020/comic-dl/releases/latest/download/comic-dl-0.0.1-1.fc44.x86_64.rpm
    sudo dnf install ./comic-dl-0.0.1-1.fc44.x86_64.rpm
    ```
    On ARM64, replace `.x86_64` with `.aarch64` in the filename.

=== "Arch Linux"

    ```bash
    curl -LO https://github.com/fallen020/comic-dl/releases/latest/download/comic-dl-0.0.1-1-x86_64.pkg.tar.zst
    sudo pacman -U comic-dl-0.0.1-1-x86_64.pkg.tar.zst
    ```

## From source

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync
```

`uv sync` installs the package and its dependencies into `.venv/`. You can then
run it from the checkout:

```bash
uv run python -m comic_dl --help
```

or through the installed console script:

```bash
.venv/bin/comic-dl --version
```

For the development environment (extra test/lint tooling), use
`uv sync --extra dev --locked` instead.

## Webview solver (Cloudflare)

The Cloudflare challenge solver is bundled — no extra install needed. It opens
a system webview (WebView2 on Windows, WKWebView on macOS, WebKitGTK on
Linux). On Linux you additionally need PyGObject and WebKitGTK from your
distro's package manager; on Windows and macOS the built-in webview works
automatically.

## Verify

With a binary install, `comic-dl` is on your PATH:

```bash
comic-dl --version
comic-dl --help
```

From a source checkout, use the venv script:

```bash
.venv/bin/comic-dl --version
```

## Shell completions

Generate completions for your shell (binary installs put `comic-dl` on PATH;
source checkouts use `.venv/bin/comic-dl`):

```bash
eval "$(comic-dl completion zsh)"    # zsh
source <(comic-dl completion bash)   # bash
comic-dl completion fish | source   # fish
```

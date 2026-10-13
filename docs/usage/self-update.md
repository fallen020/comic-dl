# Self-Management

`comic-dl self` inspects how comic-dl is installed and updates it through the
tool that owns that installation. It never guesses and never downgrades.

## Show the installed version

```
comic-dl self version [--json]
```

## Update

```
comic-dl self update [--check] [-y] [-q] [--json] [--exit-code]
```

`self update` asks the GitHub Releases API for the latest published version and
reports the installation source, installed version, latest version, and status:

```text
Installed via:        uv tool
Installed version:    <current>
Latest version:       <latest>
Status:              update available (<current> → <latest>).
Release notes:        https://github.com/fallen020/comic-dl/releases/tag/<tag>.
```

- `--check` only checks; nothing is installed or changed.
- `-y` / `--yes` skips the confirmation prompt. Without a TTY and without
  `--yes`, the update refuses rather than proceeding unattended.
- `-q` / `--quiet` suppresses progress output (lookup spinner, download bar).
- `--json` emits `{"installed", "latest", "update_available", "install_kind"}`
  and implies `--check` (same payload as `self version --json`).
- `--exit-code` exits 1 when an update is available (implies `--check`);
  pair with `--json` for cron and status bars.

### What happens, by installation type

| Installation | What `self update` does |
| :----------- | :----------------------- |
| Debian/Ubuntu (`apt`) | Downloads the `.deb` for your architecture, then runs `sudo apt install` after you confirm the sudo prompt |
| Fedora/RHEL (`dnf`) | Downloads the `.rpm`, then `sudo dnf install` |
| Arch (`pacman`) | Downloads the `.pkg.tar.zst`, then `sudo pacman -U` |
| `pip` environment | Runs `python -m pip install --upgrade comic-dl` — only when the environment is writable |
| `uv tool` | Runs `uv tool upgrade comic-dl` |
| Source checkout | Prints `git pull` and `uv sync`; never modifies the checkout |
| Standalone executable (Windows) | Downloads the published `.exe`, verifies SHA256 plus a version check, then atomically replaces itself (restart to use it) |
| Unknown | Reports that it cannot identify the installation and points at the releases page |

If you decline the confirmation (or an install fails), comic-dl prints the
exact command to run yourself. If the network or the GitHub API is
unavailable, the check reports that and leaves everything unchanged — a failed
update check never affects normal downloads.

Downloads are made over HTTPS through the same SSRF-validated, rate-limited
fetch path as scraping. Before invoking the package manager, the downloaded
artifact is checked against the release's published SHA256SUMS — a missing
or mismatched checksum refuses the install. Package installs then run
through the owning package manager (which verifies package signatures). Only visible package defaults
are used; comic-dl never adds flags like `--user` or `--break-system-packages`.
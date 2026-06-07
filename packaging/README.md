# Building & installing the MimOSA `.deb` package

This directory contains the tooling to build a Debian/Ubuntu `.deb` package for
MimOSA. The package metadata lives in the top-level [`debian/`](../debian)
directory.

## What the package does

Installing the `.deb`:

1. Pulls in the **system** dependencies automatically via `apt`
   (Python 3.10+, `python3-venv`, `python3-pip`, GTK4 + PyGObject; PortAudio /
   `libsndfile1` / `ffmpeg` are *recommended* for voice).
2. Ships the MimOSA source under `/usr/share/mimosa-assistant`.
3. Runs a `postinst` script that builds a self-contained virtual environment in
   **`/opt/mimosa/venv`**, `pip`-installs MimOSA (with the voice + UI extras),
   and symlinks the **`mimosa`** command into `/usr/bin`.
4. Adds a **MimOSA** entry to your applications menu (`mimosa.desktop`).

> **Note:** the `postinst` step uses `pip`, so it needs **network access** the
> first time it runs (to download MimOSA's Python dependencies from PyPI). This
> is the standard trade-off for a Python application whose dependencies are not
> all packaged in the Debian archive.

## Build it

From the repository root:

```bash
# One-time: install the Debian build tooling (or pass --install-build-deps).
sudo apt-get install debhelper devscripts build-essential dh-python

# Build the package.
./packaging/build-deb.sh
```

Or let the script install the build tooling for you:

```bash
./packaging/build-deb.sh --install-build-deps
```

The built package is written to the parent directory and copied to
`packaging/dist/` for convenience, e.g.:

```
packaging/dist/mimosa-assistant_1.0.0~rc1-1_all.deb
```

## Install it

```bash
sudo apt install ./packaging/dist/mimosa-assistant_1.0.0~rc1-1_all.deb
```

Using `apt install ./file.deb` (rather than `dpkg -i`) lets apt resolve and
install the system dependencies automatically.

Then:

```bash
mimosa --check     # verify the environment + show the log location
mimosa             # launch MimOSA (first run opens the setup wizard)
```

## Uninstall it

```bash
sudo apt remove mimosa-assistant      # removes the app + /opt/mimosa venv
```

Your personal data and settings under `~/.local/share/mimosa` and
`~/.config/mimosa` are **preserved**. To remove those too, run the project's
`./uninstall.sh --purge`, or delete those directories manually.

## Files in `debian/`

| File | Purpose |
|------|---------|
| `control` | Package metadata + dependencies. |
| `changelog` | Version history (Debian format). |
| `copyright` | License (MIT). |
| `rules` | Build steps (installs the source tree + `.desktop` file). |
| `postinst` | Builds the venv, installs MimOSA, creates the `mimosa` command. |
| `postrm` | Removes the venv and command on uninstall. |
| `mimosa.desktop` | Applications-menu launcher. |
| `source/format` | `3.0 (native)` source format. |

## Troubleshooting the build

- **`dh: command not found`** — install `debhelper`
  (`sudo apt-get install debhelper`) or run with `--install-build-deps`.
- **postinst fails to install Python deps** — ensure the target machine has
  network access; re-run with `sudo dpkg-reconfigure mimosa-assistant`.
- For everything else, see the project's
  [Troubleshooting guide](../docs/TROUBLESHOOTING.md).

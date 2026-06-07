#!/usr/bin/env bash
#
# build-deb.sh -- build the MimOSA .deb package from the debian/ directory.
#
# Run this from the repository root (or anywhere; it finds the repo itself):
#
#     ./packaging/build-deb.sh
#
# Options:
#     --install-build-deps   apt-install the Debian build tooling first
#                            (debhelper, devscripts, build-essential, dh-python)
#     -h | --help            show this help
#
# Output: the built .deb (and related artifacts) are placed in ../ relative to
# the repo root, and a copy of the .deb is left in packaging/dist/ for
# convenience.
set -euo pipefail

INSTALL_BUILD_DEPS=0
print_help() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-build-deps) INSTALL_BUILD_DEPS=1; shift ;;
        -h|--help) print_help; exit 0 ;;
        *) echo "Unknown option: $1" >&2; print_help; exit 1 ;;
    esac
done

# Locate the repository root (this script lives in <root>/packaging).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -d debian ]]; then
    echo "ERROR: debian/ directory not found in $REPO_ROOT" >&2
    exit 1
fi

SUDO=""
if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
fi

if [[ $INSTALL_BUILD_DEPS -eq 1 ]]; then
    echo ">> Installing Debian build dependencies via apt"
    $SUDO apt-get update -y
    $SUDO apt-get install -y debhelper devscripts build-essential dh-python
fi

# Sanity-check the tools are present.
for tool in dpkg-buildpackage dh; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: '$tool' not found. Re-run with --install-build-deps, or:" >&2
        echo "       sudo apt-get install debhelper devscripts build-essential dh-python" >&2
        exit 1
    fi
done

echo ">> Building the MimOSA .deb (this does not require network access)"
# -us -uc: do not sign the package. -b: binary-only build.
dpkg-buildpackage -us -uc -b

# Collect the artifact.
mkdir -p packaging/dist
DEB="$(ls -t ../mimosa-assistant_*_all.deb 2>/dev/null | head -n1 || true)"
if [[ -n "$DEB" && -f "$DEB" ]]; then
    cp "$DEB" packaging/dist/
    echo
    echo "============================================================"
    echo " Built: $DEB"
    echo " Copied to: packaging/dist/$(basename "$DEB")"
    echo
    echo " Install it with:"
    echo "     sudo apt install ./packaging/dist/$(basename "$DEB")"
    echo
    echo " (apt resolves the system dependencies automatically. The"
    echo "  postinst step then builds the MimOSA venv in /opt/mimosa,"
    echo "  which needs network access for PyPI.)"
    echo "============================================================"
else
    echo "WARNING: build finished but no .deb was found in ../" >&2
    exit 1
fi

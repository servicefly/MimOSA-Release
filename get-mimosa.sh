#!/usr/bin/env bash
#
# get-mimosa.sh -- the one-liner installer for MimOSA.
#
# Designed to be piped straight from the web:
#
#     curl -fsSL https://raw.githubusercontent.com/servicefly/MimOSA/develop/get-mimosa.sh | bash
#
# It will:
#     1. Make sure git is available (installing it via apt if needed).
#     2. Clone MimOSA into ~/MimOSA (or update it if already present).
#     3. Run ./bootstrap.sh, which installs all system + Python dependencies.
#
# Environment overrides:
#     MIMOSA_DIR     where to clone (default: $HOME/MimOSA)
#     MIMOSA_BRANCH  branch to clone (default: develop)
#     MIMOSA_REPO    git URL (default: https://github.com/servicefly/MimOSA.git)
#     MIMOSA_BOOTSTRAP_ARGS  extra args passed to bootstrap.sh (e.g. "--core")
#
# Privacy note: MimOSA is local-first. This script downloads code from GitHub
# and packages from your distro / PyPI only; it never sends your data anywhere.
set -euo pipefail

MIMOSA_DIR="${MIMOSA_DIR:-$HOME/MimOSA}"
MIMOSA_BRANCH="${MIMOSA_BRANCH:-develop}"
MIMOSA_REPO="${MIMOSA_REPO:-https://github.com/servicefly/MimOSA.git}"
MIMOSA_BOOTSTRAP_ARGS="${MIMOSA_BOOTSTRAP_ARGS:-}"

if [ -t 1 ]; then
    BOLD="$(printf '\033[1m')"; GREEN="$(printf '\033[32m')"; RESET="$(printf '\033[0m')"
else
    BOLD=""; GREEN=""; RESET=""
fi
step() { echo "${BOLD}==> $*${RESET}"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

echo
echo "${BOLD}MimOSA one-liner installer${RESET}"
echo "=========================="
echo

# --- 1. Ensure git is available ------------------------------------------
if ! command -v git >/dev/null 2>&1; then
    step "git not found -- installing it"
    if command -v apt-get >/dev/null 2>&1; then
        SUDO=""
        [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
        $SUDO apt-get update -y || true
        $SUDO apt-get install -y git || fail "Could not install git. Install it manually and re-run."
    else
        fail "git is required but not installed, and apt is unavailable.
       Please install git with your package manager and re-run."
    fi
fi

# --- 2. Clone or update the repository -----------------------------------
if [ -d "$MIMOSA_DIR/.git" ]; then
    step "Updating existing MimOSA checkout in $MIMOSA_DIR"
    git -C "$MIMOSA_DIR" fetch --depth=1 origin "$MIMOSA_BRANCH" || true
    git -C "$MIMOSA_DIR" checkout "$MIMOSA_BRANCH" || true
    git -C "$MIMOSA_DIR" pull --ff-only origin "$MIMOSA_BRANCH" || true
else
    step "Cloning MimOSA into $MIMOSA_DIR"
    git clone --depth=1 --branch "$MIMOSA_BRANCH" "$MIMOSA_REPO" "$MIMOSA_DIR" \
        || fail "Failed to clone $MIMOSA_REPO (branch $MIMOSA_BRANCH)."
fi

cd "$MIMOSA_DIR"

# --- 3. Hand off to bootstrap.sh -----------------------------------------
if [ ! -x ./bootstrap.sh ]; then
    [ -f ./bootstrap.sh ] && chmod +x ./bootstrap.sh || fail "bootstrap.sh missing from the repository."
fi

step "Running bootstrap.sh ${MIMOSA_BOOTSTRAP_ARGS}"
echo
# shellcheck disable=SC2086
exec ./bootstrap.sh ${MIMOSA_BOOTSTRAP_ARGS}

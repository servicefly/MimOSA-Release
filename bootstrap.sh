#!/usr/bin/env bash
#
# MimOSA bootstrap -- the easiest possible install for newcomers on a fresh
# Ubuntu / Kubuntu machine. It does everything for you:
#
#     1. Confirms you are on a Debian/Ubuntu-based system (apt available).
#     2. Installs every SYSTEM dependency MimOSA can use (Python 3.11+, pip,
#        venv, git, PortAudio for voice, GTK4 for the avatar) via apt.
#     3. Hands off to the existing ./install.sh to set up the Python side.
#
# Just run:
#
#     ./bootstrap.sh                 # install everything (recommended)
#     ./bootstrap.sh --core          # skip voice + GUI system packages
#     ./bootstrap.sh --no-voice      # skip only the PortAudio (voice) packages
#     ./bootstrap.sh --no-ui         # skip only the GTK4 (avatar) packages
#     ./bootstrap.sh --yes           # never prompt (assume "yes")
#     ./bootstrap.sh -h | --help     # show this help
#
# Privacy note: MimOSA is local-first. This script installs software from your
# distribution and PyPI only; it never sends any of your data anywhere.
set -euo pipefail

# --------------------------------------------------------------------------
# Pretty output helpers
# --------------------------------------------------------------------------
if [[ -t 1 ]]; then
    BOLD="$(printf '\033[1m')"; GREEN="$(printf '\033[32m')"
    YELLOW="$(printf '\033[33m')"; RED="$(printf '\033[31m')"
    BLUE="$(printf '\033[34m')"; RESET="$(printf '\033[0m')"
else
    BOLD=""; GREEN=""; YELLOW=""; RED=""; BLUE=""; RESET=""
fi

step()  { echo "${BLUE}${BOLD}==>${RESET} ${BOLD}$*${RESET}"; }
info()  { echo "    $*"; }
ok()    { echo "${GREEN}    ✔ $*${RESET}"; }
warn()  { echo "${YELLOW}    ! $*${RESET}" >&2; }
fail()  { echo "${RED}${BOLD}ERROR:${RESET} $*" >&2; exit 1; }

# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
WITH_VOICE=1
WITH_UI=1
ASSUME_YES=0

print_help() { sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --core)     WITH_VOICE=0; WITH_UI=0; shift ;;
        --no-voice) WITH_VOICE=0; shift ;;
        --no-ui)    WITH_UI=0; shift ;;
        --yes|-y)   ASSUME_YES=1; shift ;;
        -h|--help)  print_help; exit 0 ;;
        *) echo "Unknown option: $1" >&2; print_help; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo
echo "${BOLD}MimOSA bootstrap installer${RESET}"
echo "${BOLD}==========================${RESET}"
echo "This will install MimOSA and the system packages it needs."
echo

# --------------------------------------------------------------------------
# 1. Confirm we are on a Debian/Ubuntu-based system
# --------------------------------------------------------------------------
step "Checking your operating system"
if ! command -v apt-get >/dev/null 2>&1; then
    fail "This bootstrap script only supports Debian/Ubuntu-based systems
       (it uses 'apt'). On another distribution, install Python 3.10+,
       pip, venv, git, PortAudio and GTK4 with your package manager, then
       run ./install.sh directly. See INSTALL.md for details."
fi

DISTRO="your system"
DISTRO_ID=""
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO="${PRETTY_NAME:-$NAME}"
    DISTRO_ID="${ID:-}${ID_LIKE:+ ${ID_LIKE}}"
fi
ok "Detected: ${DISTRO}"
case "${DISTRO_ID}" in
    *ubuntu*|*debian*) : ;;  # expected
    *) warn "This looks like a non-Ubuntu system. apt-based install will be
       attempted anyway; if it fails, follow INSTALL.md manually." ;;
esac

# --------------------------------------------------------------------------
# 2. Figure out whether we need sudo
# --------------------------------------------------------------------------
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
        info "Some steps need administrator rights; you may be asked for your"
        info "password by 'sudo'."
    else
        fail "Need root privileges to install system packages, but 'sudo' is
       not available. Re-run this script as root, or install sudo."
    fi
fi

# --------------------------------------------------------------------------
# 3. Decide which apt packages to install
# --------------------------------------------------------------------------
# Always-needed base packages.
APT_PACKAGES=(python3 python3-venv python3-pip git)

# Prefer an explicit modern Python if the repo provides it; harmless if absent.
APT_OPTIONAL=(python3.11 python3.11-venv)

if [[ $WITH_VOICE -eq 1 ]]; then
    APT_PACKAGES+=(portaudio19-dev libsndfile1 ffmpeg)
fi
if [[ $WITH_UI -eq 1 ]]; then
    APT_PACKAGES+=(libgtk-4-1 gir1.2-gtk-4.0 libgirepository-1.0-1 \
                   gir1.2-glib-2.0 python3-gi python3-gi-cairo)
fi

echo
step "Packages to install via apt"
info "Base:  python3 python3-venv python3-pip git"
[[ $WITH_VOICE -eq 1 ]] && info "Voice: portaudio19-dev libsndfile1 ffmpeg" || warn "Voice packages skipped (--no-voice/--core)"
[[ $WITH_UI -eq 1 ]]    && info "UI:    GTK4 + PyGObject bindings"          || warn "UI packages skipped (--no-ui/--core)"
echo

if [[ $ASSUME_YES -ne 1 ]]; then
    printf "Proceed with installation? [Y/n] "
    read -r REPLY || REPLY=""
    case "$REPLY" in
        [nN]*) echo "Aborted."; exit 0 ;;
    esac
fi

# --------------------------------------------------------------------------
# 4. Install system packages
# --------------------------------------------------------------------------
step "Updating package lists (apt-get update)"
if ! $SUDO apt-get update -y; then
    warn "apt-get update reported problems; continuing anyway."
fi

step "Installing optional modern Python (best-effort)"
# These may not exist on every release; never fail the whole run for them.
if ! $SUDO apt-get install -y "${APT_OPTIONAL[@]}" 2>/dev/null; then
    info "python3.11 packages not available here; using the system python3."
fi

step "Installing required system packages"
if ! $SUDO apt-get install -y "${APT_PACKAGES[@]}"; then
    fail "Failed to install one or more system packages. Scroll up for the
       apt error, fix it (e.g. 'sudo apt-get update'), and re-run
       ./bootstrap.sh. You can also retry with --core to skip the optional
       voice/UI packages."
fi
ok "System packages installed."

# --------------------------------------------------------------------------
# 5. Verify Python version
# --------------------------------------------------------------------------
step "Verifying Python version"
PY="python3"
if command -v python3.11 >/dev/null 2>&1; then
    PY="python3.11"
fi
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    fail "MimOSA needs Python 3.10+, but '$PY' is $PYVER. Install a newer
       Python (e.g. 'sudo apt-get install python3.11 python3.11-venv') and
       re-run ./bootstrap.sh."
fi
ok "Using Python $PYVER ($PY)"

# --------------------------------------------------------------------------
# 6. Hand off to install.sh for the Python side
# --------------------------------------------------------------------------
if [[ ! -x ./install.sh ]]; then
    if [[ -f ./install.sh ]]; then
        chmod +x ./install.sh
    else
        fail "install.sh not found next to bootstrap.sh. Run this script from
       inside the cloned MimOSA repository."
    fi
fi

INSTALL_ARGS=()
if [[ $WITH_VOICE -eq 1 && $WITH_UI -eq 1 ]]; then
    INSTALL_ARGS+=(--with-all)
elif [[ $WITH_VOICE -eq 1 ]]; then
    INSTALL_ARGS+=(--with-voice)
elif [[ $WITH_UI -eq 1 ]]; then
    INSTALL_ARGS+=(--with-ui)
fi

echo
step "Running ./install.sh ${INSTALL_ARGS[*]:-(core only)}"
echo
# Make install.sh use the Python we verified above.
PYTHON="$PY" ./install.sh "${INSTALL_ARGS[@]}"

echo
echo "${GREEN}${BOLD}All done!${RESET} MimOSA and its dependencies are installed."
echo "Next:"
echo "    ${BOLD}source .venv/bin/activate${RESET}"
echo "    ${BOLD}mimosa --check${RESET}     # verify the environment"
echo "    ${BOLD}mimosa${RESET}             # launch MimOSA"
echo
echo "The first launch runs the friendly \"Get to Know MimOSA\" setup wizard."
echo "Need help? See INSTALL.md and docs/TROUBLESHOOTING.md."

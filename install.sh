#!/usr/bin/env bash
#
# MimOSA installer -- sets up a self-contained virtual environment and installs
# MimOSA into it. Designed to be as easy as possible: just run
#
#     ./install.sh
#
# Options:
#     --with-voice    also install the local voice pipeline (Whisper/Piper/etc.)
#     --with-ui       also install the GTK4 desktop avatar dependencies
#     --with-all      install voice + UI extras
#     --venv DIR      use a custom virtualenv directory (default: .venv)
#     -h | --help     show this help
#
# Privacy note: MimOSA is local-first. This script only downloads Python
# packages from PyPI; it never sends any of your data anywhere.
set -euo pipefail

VENV_DIR=".venv"
EXTRAS=""
WITH_VOICE=0
WITH_UI=0

print_help() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-voice) WITH_VOICE=1; shift ;;
        --with-ui)    WITH_UI=1; shift ;;
        --with-all)   WITH_VOICE=1; WITH_UI=1; shift ;;
        --venv)       VENV_DIR="${2:?--venv needs a directory}"; shift 2 ;;
        -h|--help)    print_help; exit 0 ;;
        *) echo "Unknown option: $1" >&2; print_help; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- 1. Check Python ------------------------------------------------------
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install Python 3.10+ and re-run." >&2
    exit 1
fi
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo ">> Using Python $PYVER ($PY)"
"$PY" - <<'PYCHECK' || { echo "ERROR: Python 3.10+ is required." >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PYCHECK

# --- 2. Create / reuse the virtual environment ----------------------------
if [[ ! -d "$VENV_DIR" ]]; then
    echo ">> Creating virtual environment in $VENV_DIR"
    "$PY" -m venv "$VENV_DIR"
else
    echo ">> Reusing existing virtual environment in $VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# --- 3. Install MimOSA ----------------------------------------------------
echo ">> Upgrading pip"
python -m pip install --upgrade pip >/dev/null

if [[ $WITH_VOICE -eq 1 && $WITH_UI -eq 1 ]]; then
    EXTRAS="[all]"
elif [[ $WITH_VOICE -eq 1 ]]; then
    EXTRAS="[voice]"
elif [[ $WITH_UI -eq 1 ]]; then
    EXTRAS="[ui]"
fi

echo ">> Installing MimOSA${EXTRAS:+ with extras $EXTRAS}"
python -m pip install -e ".${EXTRAS}"

# --- 4. Done --------------------------------------------------------------
cat <<EOF

============================================================================
 MimOSA installed successfully (release candidate 1.0.0rc1).

 To start MimOSA:
     source $VENV_DIR/bin/activate
     mimosa                # GUI avatar if GTK is available, else headless
     mimosa --no-gui       # force headless (voice/CLI only)
     mimosa --check        # print environment + log-file readiness

 First launch runs the "Get to Know MimOSA" setup wizard.

 Logs:   ~/.local/share/mimosa/logs/mimosa.log   (rotated, capped)
 Data:   ~/.local/share/mimosa/                   (databases, models)
 Config: ~/.config/mimosa/settings.json

 Optional extras you can add later:
     ./install.sh --with-voice    # local speech recognition + TTS
     ./install.sh --with-ui       # GTK4 desktop avatar
     ./install.sh --with-all      # both

 See INSTALL.md for system packages (PortAudio, GTK) and troubleshooting.
============================================================================
EOF

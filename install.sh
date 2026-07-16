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
#
# On Debian/Ubuntu the GTK4 Python bindings (PyGObject / python3-gi) are
# provided as *system* apt packages, and their typelibs cannot be reliably
# pip-built inside an isolated venv. If the venv cannot see the system
# packages, `import gi` fails at runtime and MimOSA silently drops to headless
# mode -- no window and no setup wizard ever appear. We therefore create the
# venv with --system-site-packages so the apt-installed GTK4 bindings are
# visible. pip still installs MimOSA's own dependencies into the venv.
VENV_OPTS=()
if [[ $WITH_UI -eq 1 ]]; then
    VENV_OPTS+=(--system-site-packages)
fi

if [[ ! -d "$VENV_DIR" ]]; then
    echo ">> Creating virtual environment in $VENV_DIR ${VENV_OPTS[*]:-}"
    "$PY" -m venv "${VENV_OPTS[@]}" "$VENV_DIR"
else
    echo ">> Reusing existing virtual environment in $VENV_DIR"
    # Repair an existing venv that was created without system site-packages
    # (older installs) so it can see the system GTK4 bindings. This is what
    # makes the window/wizard appear on machines upgraded from a broken build.
    if [[ $WITH_UI -eq 1 && -f "$VENV_DIR/pyvenv.cfg" ]]; then
        if grep -qi '^include-system-site-packages *= *false' "$VENV_DIR/pyvenv.cfg"; then
            echo ">> Enabling system site-packages in existing venv (needed for GTK4)"
            sed -i 's/^include-system-site-packages *= *false/include-system-site-packages = true/I' \
                "$VENV_DIR/pyvenv.cfg"
        elif ! grep -qi '^include-system-site-packages' "$VENV_DIR/pyvenv.cfg"; then
            echo "include-system-site-packages = true" >> "$VENV_DIR/pyvenv.cfg"
        fi
    fi
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
# --no-warn-conflicts: on a re-install openWakeWord is already present without
# tflite-runtime (by design -- see below), which would otherwise make pip print
# a scary but harmless conflict notice here.
python -m pip install --no-warn-conflicts -e ".${EXTRAS}"

# --- 3a. openWakeWord (without its tflite-runtime dependency) --------------
# openWakeWord lists `tflite-runtime` as a mandatory Linux dependency, but that
# wheel is only published for CPython <= 3.11. On Python 3.12+ (Ubuntu/Kubuntu
# 24.04's default) a normal `pip install openwakeword` aborts the whole install.
# openWakeWord runs fine on onnxruntime alone, so we install it with --no-deps
# and provide its real runtime dependencies explicitly (minus tflite-runtime).
# MimOSA forces the ONNX backend at runtime (see mimosa/voice/wake_word.py).
if [[ $WITH_VOICE -eq 1 ]]; then
    echo ">> Installing openWakeWord (ONNX backend, without tflite-runtime)"
    # --no-warn-conflicts silences pip's "openwakeword requires tflite-runtime,
    # which is not installed" notice. That notice is EXPECTED and harmless here:
    # we intentionally omit tflite-runtime (no wheels for Python 3.12+) and run
    # openWakeWord on onnxruntime instead. The notice does not mean the install
    # failed.
    if ! python -m pip install --no-deps --no-warn-conflicts "openwakeword>=0.6,<0.7"; then
        echo "   ! openWakeWord install failed; MimOSA will use the energy-based" >&2
        echo "     wake-word fallback. Voice still works." >&2
    else
        # openWakeWord's real runtime deps (tflite-runtime intentionally omitted).
        python -m pip install --no-warn-conflicts \
            "onnxruntime>=1.10,<2" "tqdm>=4.0,<5.0" "scipy>=1.3,<2" \
            "scikit-learn>=1,<2" "requests>=2.0,<3" \
            || echo "   ! Some openWakeWord runtime deps failed to install." >&2
        echo "   (If pip printed a 'tflite-runtime is not installed' notice above,"
        echo "    it is expected and safe -- MimOSA uses the ONNX backend.)"
    fi
fi

# --- 3b. Desktop integration (menu entry + app icon) ----------------------
# Install a .desktop launcher and the app icon into the per-user XDG data dirs
# so MimOSA shows up (with its real icon, not a generic placeholder) in the
# applications menu. All paths are under $HOME -- no root/sudo required.
install_desktop_integration() {
    local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    local apps_dir="$data_home/applications"
    local icons_base="$data_home/icons/hicolor"
    local assets="$SCRIPT_DIR/mimosa/ui/assets"
    local mimosa_bin="$VENV_DIR/bin/mimosa"

    # Resolve the venv's mimosa launcher to an absolute path so the menu entry
    # works without activating the virtualenv first.
    case "$mimosa_bin" in
        /*) : ;;                          # already absolute
        *)  mimosa_bin="$SCRIPT_DIR/$mimosa_bin" ;;
    esac

    echo ">> Installing desktop launcher and icon"
    mkdir -p "$apps_dir"

    # Raster icons into the hicolor theme as "mimosa.png" (matches Icon=mimosa).
    local sz
    for sz in 16 32 48 128 256; do
        if [[ -f "$assets/icons/mimosa-$sz.png" ]]; then
            mkdir -p "$icons_base/${sz}x${sz}/apps"
            cp "$assets/icons/mimosa-$sz.png" \
               "$icons_base/${sz}x${sz}/apps/mimosa.png"
        fi
    done
    # Scalable SVG variant for crisp rendering at any size.
    if [[ -f "$assets/mimosa-icon.svg" ]]; then
        mkdir -p "$icons_base/scalable/apps"
        cp "$assets/mimosa-icon.svg" "$icons_base/scalable/apps/mimosa.svg"
    fi
    # Pixmap fallback for older menu implementations.
    if [[ -f "$assets/icons/mimosa-128.png" ]]; then
        mkdir -p "$data_home/pixmaps"
        cp "$assets/icons/mimosa-128.png" "$data_home/pixmaps/mimosa.png"
    fi

    # Write the .desktop entry, pointing Exec at the absolute venv launcher.
    cat > "$apps_dir/mimosa.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=MimOSA
GenericName=Voice Assistant
Comment=Privacy-first, local-first voice assistant for Linux
Exec=$mimosa_bin
Icon=mimosa
Terminal=false
Categories=Utility;Accessibility;AudioVideo;
Keywords=voice;assistant;ai;privacy;
StartupNotify=false
DESKTOP
    chmod 644 "$apps_dir/mimosa.desktop"

    # Refresh the desktop database and icon cache so the entry/icon appear
    # immediately (all best-effort; missing tools are not fatal).
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$apps_dir" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 \
        && gtk-update-icon-cache -f -t "$icons_base" >/dev/null 2>&1 || true

    echo ">> Desktop launcher installed at $apps_dir/mimosa.desktop"
}
install_desktop_integration || echo "WARNING: desktop integration step failed (non-fatal)." >&2

# --- 3c. Global PATH access (Bug #12) -------------------------------------
# The `mimosa` launcher lives inside the virtualenv's bin/ dir, which is only
# on PATH while the venv is activated. To let users run `mimosa` from anywhere
# without activating the venv, symlink it into ~/.local/bin (the standard XDG
# per-user bin dir) and make sure that dir is on PATH for future shells.
PATH_HINT=""
install_path_launcher() {
    local bin_dir="$HOME/.local/bin"
    local mimosa_bin="$VENV_DIR/bin/mimosa"
    case "$mimosa_bin" in
        /*) : ;;
        *)  mimosa_bin="$SCRIPT_DIR/$mimosa_bin" ;;
    esac

    if [[ ! -x "$mimosa_bin" ]]; then
        echo "WARNING: $mimosa_bin not found; skipping PATH setup." >&2
        return 0
    fi

    mkdir -p "$bin_dir"
    ln -sf "$mimosa_bin" "$bin_dir/mimosa"
    echo ">> Linked 'mimosa' into $bin_dir"

    # Ensure ~/.local/bin is on PATH. If it already is, nothing to do.
    case ":$PATH:" in
        *":$bin_dir:"*) return 0 ;;
    esac

    # Append a PATH export to the user's shell rc files (idempotent).
    local added=0 rc line
    line='export PATH="$HOME/.local/bin:$PATH"'
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        [[ -f "$rc" ]] || continue
        if ! grep -qsF "$line" "$rc"; then
            {
                echo ""
                echo "# Added by MimOSA installer so 'mimosa' is on PATH"
                echo "$line"
            } >> "$rc"
            added=1
        fi
    done
    # Always make sure at least ~/.profile carries it (login shells).
    if [[ $added -eq 0 && ! -f "$HOME/.bashrc" && ! -f "$HOME/.zshrc" ]]; then
        {
            echo ""
            echo "# Added by MimOSA installer so 'mimosa' is on PATH"
            echo "$line"
        } >> "$HOME/.profile"
    fi
    PATH_HINT="$bin_dir was added to your PATH. Open a new terminal (or run \
'source ~/.bashrc') before using the 'mimosa' command."
}
install_path_launcher || echo "WARNING: PATH setup step failed (non-fatal)." >&2

# --- 3d. Verify the GTK4 desktop UI is actually importable ----------------
# The most common "it installed but nothing opens" cause is that GTK4/PyGObject
# is not importable from the venv, so MimOSA silently runs headless (no window,
# no wizard). Check now and tell the user exactly how to fix it if so.
GTK_OK=0
if [[ $WITH_UI -eq 1 ]]; then
    if python - <<'GTKCHECK' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: F401
raise SystemExit(0)
GTKCHECK
    then
        GTK_OK=1
        echo ">> GTK4 desktop UI is available (the avatar window + wizard will show)."
    else
        echo "" >&2
        echo "WARNING: GTK4 / PyGObject could not be imported from the venv." >&2
        echo "         MimOSA will run HEADLESS (no window, no setup wizard)." >&2
        echo "         Fix it with the system GTK4 bindings, then re-run this script:" >&2
        echo "           sudo apt-get install -y python3-gi python3-gi-cairo \\" >&2
        echo "               gir1.2-gtk-4.0 libgtk-4-1" >&2
        echo "           ./install.sh --with-ui" >&2
    fi
fi

# --- 4. Done --------------------------------------------------------------
cat <<EOF

============================================================================
 MimOSA installed successfully (v2.0.0-beta).

 To start MimOSA (from any directory -- it's on your PATH now):
     mimosa                # GUI avatar if GTK is available, else headless
     mimosa --no-gui       # force headless (voice/CLI only)
     mimosa --check        # print environment + log-file readiness
     mimosa --check-audio  # test your microphone

 ${PATH_HINT:-The 'mimosa' command is available in this shell.}

 (You can also still run it via: source $VENV_DIR/bin/activate && mimosa)

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

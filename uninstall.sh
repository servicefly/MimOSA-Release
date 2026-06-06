#!/usr/bin/env bash
#
# MimOSA uninstaller -- removes the virtual environment and, optionally, your
# local data and configuration.
#
#     ./uninstall.sh                 remove the venv only (keep data + config)
#     ./uninstall.sh --purge         also delete data + config (asks first)
#     ./uninstall.sh --purge --yes   purge without the confirmation prompt
#     --venv DIR                     custom virtualenv dir (default: .venv)
#     -h | --help                    show this help
#
# Your data lives under the XDG locations below; --purge removes them so an
# uninstall can be made completely clean.
set -euo pipefail

VENV_DIR=".venv"
PURGE=0
ASSUME_YES=0

DATA_DIR="${MIMOSA_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/mimosa}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mimosa"

print_help() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=1; shift ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --venv)  VENV_DIR="${2:?--venv needs a directory}"; shift 2 ;;
        -h|--help) print_help; exit 0 ;;
        *) echo "Unknown option: $1" >&2; print_help; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- 1. Remove the virtual environment ------------------------------------
if [[ -d "$VENV_DIR" ]]; then
    echo ">> Removing virtual environment $VENV_DIR"
    rm -rf "$VENV_DIR"
else
    echo ">> No virtual environment at $VENV_DIR (skipping)"
fi

# --- 2. Optionally purge data + config ------------------------------------
if [[ $PURGE -eq 1 ]]; then
    echo
    echo "The following directories will be PERMANENTLY deleted:"
    echo "    $DATA_DIR"
    echo "    $CONFIG_DIR"
    if [[ $ASSUME_YES -ne 1 ]]; then
        read -r -p "Are you sure? [y/N] " reply
        case "$reply" in
            [yY]|[yY][eE][sS]) ;;
            *) echo "Aborted; data and config kept."; exit 0 ;;
        esac
    fi
    rm -rf "$DATA_DIR" "$CONFIG_DIR"
    echo ">> Removed data and config."
else
    echo
    echo "Kept your data and config. To remove them too, run:"
    echo "    ./uninstall.sh --purge"
    echo "  Data:   $DATA_DIR"
    echo "  Config: $CONFIG_DIR"
fi

echo ">> MimOSA uninstalled."

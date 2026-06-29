#!/usr/bin/env bash
# install.sh — CorvinOS installer for Linux and macOS.
# Usage:
#   curl -fsSL https://corvin-labs.com/install.sh | bash
#   bash install.sh --editable /path/to/CorvinOS   # dev install from local clone
set -euo pipefail

VENV_DIR="${HOME}/corvin_venv"
PACKAGE="corvinos"
EDITABLE_PATH=""

# ── helpers ───────────────────────────────────────────────────────────────────

_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_dim()   { printf '\033[2m%s\033[0m' "$*"; }

die() { echo "$(_red "Error:") $*" >&2; exit 1; }

trap 'echo "" >&2; echo "$(_red "Installation failed.") See the error above." >&2' ERR

# ── argument parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        -e|--editable)
            [[ $# -lt 2 ]] && die "--editable requires a path argument"
            EDITABLE_PATH="$2"
            shift 2
            ;;
        *)
            die "Unknown argument: $1
Usage: $0 [--editable|-e <path>]
  --editable <path>   Install in editable mode from a local clone (dev only)"
            ;;
    esac
done

if [[ -n "$EDITABLE_PATH" ]]; then
    [[ -d "$EDITABLE_PATH" ]] || die "Editable path does not exist: $EDITABLE_PATH"
    EDITABLE_PATH="$(cd "$EDITABLE_PATH" && pwd)"
fi

# ── Python version check ──────────────────────────────────────────────────────

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "0")
        major=$("$candidate" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo "0")
        if [ "$major" -eq 3 ] && [ "$version" -ge 10 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    die "Python 3.10+ is required but not found.
  Linux:  sudo apt install python3  (or your distro's package manager)
  macOS:  brew install python3  (https://brew.sh)
  Or:     https://www.python.org/downloads/"
fi

PYTHON_VER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "$(_bold "CorvinOS installer")"
echo "  Python $PYTHON_VER — $(_green "OK")"

# ── venv availability check ───────────────────────────────────────────────────

if ! "$PYTHON" -c "import venv" 2>/dev/null; then
    die "Python 'venv' module is missing.
  Ubuntu/Debian:  sudo apt install python3-venv
  Fedora/RHEL:    sudo dnf install python3
  Arch:           python already includes venv
  macOS:          brew install python3 (already includes venv)"
fi

# ── virtual environment ───────────────────────────────────────────────────────

echo "  Creating virtual environment at $VENV_DIR ..."
"$PYTHON" -m venv "$VENV_DIR"

PIP="$VENV_DIR/bin/pip"

# pip is normally bundled via ensurepip, but some Linux distros strip it out
# (e.g. Ubuntu python3-venv without python3-pip). Bootstrap it if missing.
if [ ! -f "$PIP" ]; then
    echo "  pip not bundled — bootstrapping via ensurepip ..."
    "$VENV_DIR/bin/python" -m ensurepip --upgrade 2>/dev/null || \
        die "Could not bootstrap pip in the virtual environment.
  Ubuntu/Debian:  sudo apt install python3-pip
  Fedora/RHEL:    sudo dnf install python3-pip"
fi

# ── install package ───────────────────────────────────────────────────────────

echo "  Upgrading pip ..."
"$PIP" install --quiet --upgrade pip

if [[ -n "$EDITABLE_PATH" ]]; then
    echo "  Installing CorvinOS in editable mode from $EDITABLE_PATH ..."
    "$PIP" install -e "$EDITABLE_PATH"
else
    echo "  Installing $PACKAGE ..."
    "$PIP" install "$PACKAGE"
fi

CORVIN_SERVE_BIN="$VENV_DIR/bin/corvinos-serve"
if [ ! -x "$CORVIN_SERVE_BIN" ]; then
    die "pip install succeeded but 'corvinos-serve' not found at $CORVIN_SERVE_BIN"
fi

# ── PATH setup ────────────────────────────────────────────────────────────────

BIN_DIR="$VENV_DIR/bin"
EXPORT_LINE="export PATH=\"$BIN_DIR:\$PATH\""

_add_to_profile() {
    local profile="$1"
    if [ -f "$profile" ] && ! grep -qF "$BIN_DIR" "$profile"; then
        echo "" >> "$profile"
        echo "# Added by CorvinOS installer" >> "$profile"
        echo "$EXPORT_LINE" >> "$profile"
        echo "  Added PATH entry to $profile"
    fi
}

_add_to_profile "${HOME}/.bashrc"
_add_to_profile "${HOME}/.zshrc"
_add_to_profile "${HOME}/.profile"

# ── run setup wizard ──────────────────────────────────────────────────────────

trap - ERR

CORVIN_INSTALL_BIN="$VENV_DIR/bin/corvin-install"

echo ""
echo "  $(_green "$(_bold "Package installed.")")"

if [ -x "$CORVIN_INSTALL_BIN" ]; then
    if [ -t 0 ]; then
        echo "  Launching setup wizard ..."
        echo ""
        "$CORVIN_INSTALL_BIN"
    else
        echo ""
        echo "  $(_yellow "Note:") Detected non-interactive shell (e.g. piped via curl)."
        echo "  Run the setup wizard once your terminal is ready:"
        echo ""
        echo "    $(_bold "corvin-install")"
    fi
fi

# ── done / cheat sheet ────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " $(_green "$(_bold "CorvinOS is ready!")")"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo " $(_bold "Step 1 — Open a new terminal window") (so PATH is updated)"
echo "         Or activate right now without restarting:"
echo ""
echo "           source $VENV_DIR/bin/activate"
echo "           $(_dim "# Type 'deactivate' to leave the environment again")"
echo ""
echo " $(_bold "Step 2 — Start the web console")"
echo ""
echo "           $(_bold "corvinos-serve")"
echo "           $(_dim "# Then open:  http://localhost:8765/console/")"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " $(_bold "All available commands")"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "   $(_bold "corvinos-serve")      Start the web console"
echo "   $(_bold "corvin-install")      Run the setup wizard (bridges, tokens, voice)"
echo "   $(_bold "corvin-uninstall")    Remove CorvinOS (services, plugins, config)"
echo "   $(_bold "corvin-restore")      Restore a previous installation"
echo "   $(_bold "corvin-flow")         Manage declarative multi-node workflows"
echo "   $(_bold "corvin-layer")        Manage layer extensions"
echo "   $(_bold "corvin-a2a")          Agent-to-agent pairing and messaging"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " $(_bold "Optional: local AI model")  $(_dim "(for offline / private use)")"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "   $(_bold "ollama pull qwen3:8b")     5.2 GB  — enables /engine hermes"
echo "   $(_bold "ollama pull qwen3:1.7b")   1.4 GB  — lighter/faster variant"
echo "   $(_dim "Skip if you only use cloud engines (Claude, Codex, Copilot).")"
echo ""

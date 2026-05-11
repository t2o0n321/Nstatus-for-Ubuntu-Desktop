#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# NStatus install.sh
#
# Installs the NStatus network monitor for the current user.
#
# What this script does:
#   1. Checks system dependencies (python3, pip, conky, curl, ping, speedtest-cli)
#   2. Creates the config directory at ~/.config/nstatus
#   3. Copies project files into the config directory
#   4. Creates a Python virtual environment at ~/.local/share/nstatus/venv
#   5. Installs Python dependencies into the venv
#   6. Installs user-level systemd service units
#   7. Enables and starts nstatus.service, nstatus-conky.service, and nstatus-toggle.service
#
# Usage:
#   cd /path/to/nstatus-project
#   bash scripts/install.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────── #
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

# ── Paths ─────────────────────────────────────────────────────────────────── #
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${HOME}/.config/nstatus"
DATA_DIR="${HOME}/.local/share/nstatus"
VENV_DIR="${DATA_DIR}/venv"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo -e "\n${BOLD}╔══════════════════════════════════╗"
echo -e "║   NStatus Network Monitor        ║"
echo -e "║   Installation Script            ║"
echo -e "╚══════════════════════════════════╝${RESET}\n"

# ── Step 1: Check dependencies ────────────────────────────────────────────── #
info "Checking system dependencies…"

MISSING=()

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        MISSING+=("$1")
        warn "  ✗  $1 — NOT FOUND"
    else
        success "  ✓  $1"
    fi
}

check_cmd python3
check_cmd pip3
check_cmd ping
check_cmd curl
check_cmd conky
check_cmd dig     # dnsutils — for DNS latency measurement
check_cmd xwininfo  # x11-utils — for toggle button window positioning

# Check python3-gi (GTK bindings for the toggle button)
if ! python3 -c "import gi" &>/dev/null; then
    MISSING+=("python3-gi")
    warn "  ✗  python3-gi — NOT FOUND (required for toggle button)"
else
    success "  ✓  python3-gi"
fi

# Optional but recommended
if ! command -v speedtest-cli &>/dev/null; then
    warn "  ⚠  speedtest-cli not found — will be installed via pip"
fi
if ! command -v iperf3 &>/dev/null; then
    info "  ℹ  iperf3 not found — only needed if throughput.method=iperf3"
fi
if ! command -v ip &>/dev/null; then
    warn "  ⚠  'ip' (iproute2) not found — gateway detection will be disabled"
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo
    warn "Missing required packages: ${MISSING[*]}"
    echo -e "Install with:\n  sudo apt update && sudo apt install -y python3 python3-pip python3-venv python3-gi gir1.2-gtk-3.0 conky-all curl iputils-ping dnsutils iproute2 x11-utils"
    die "Please install missing packages and re-run install.sh"
fi

# ── Step 2: Create install directory ─────────────────────────────────────── #
info "Creating install directory: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
mkdir -p "${DATA_DIR}/logs"

# ── Step 3: Copy project files ────────────────────────────────────────────── #
info "Copying project files to ${INSTALL_DIR}…"

# Copy source tree
cp -r "${REPO_DIR}/src"     "${INSTALL_DIR}/"
cp -r "${REPO_DIR}/conky"   "${INSTALL_DIR}/"
cp -r "${REPO_DIR}/scripts" "${INSTALL_DIR}/"
chmod +x "${INSTALL_DIR}/scripts"/*.sh \
         "${INSTALL_DIR}/scripts/pppoe"/*.sh \
         "${INSTALL_DIR}/scripts/ipoe"/*.sh 2>/dev/null || true

# Copy default config if user doesn't have one yet
if [ ! -f "${INSTALL_DIR}/config.yaml" ]; then
    cp "${REPO_DIR}/config/config.yaml" "${INSTALL_DIR}/config.yaml"
    success "Default config.yaml installed at ${INSTALL_DIR}/config.yaml"
else
    info "Existing config.yaml kept (not overwritten)"
fi

# ── Step 4: Python virtual environment ───────────────────────────────────── #
info "Creating Python virtual environment at ${VENV_DIR}…"
python3 -m venv "${VENV_DIR}"

info "Installing Python dependencies…"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet \
    pyyaml \
    speedtest-cli
success "Python dependencies installed"

# ── Step 5: Install systemd user units ───────────────────────────────────── #
info "Installing systemd user units to ${SYSTEMD_USER_DIR}…"
mkdir -p "${SYSTEMD_USER_DIR}"

cp "${REPO_DIR}/systemd/nstatus.service"        "${SYSTEMD_USER_DIR}/nstatus.service"
cp "${REPO_DIR}/systemd/nstatus-conky.service"  "${SYSTEMD_USER_DIR}/nstatus-conky.service"
cp "${REPO_DIR}/systemd/nstatus-toggle.service" "${SYSTEMD_USER_DIR}/nstatus-toggle.service"

# Reload systemd user daemon
systemctl --user daemon-reload
success "systemd units installed and daemon reloaded"

# ── Step 6: Enable and start services ────────────────────────────────────── #
info "Enabling nstatus.service (auto-start on login)…"
systemctl --user enable nstatus.service
systemctl --user enable nstatus-conky.service
systemctl --user enable nstatus-toggle.service

info "Starting nstatus.service…"
systemctl --user restart nstatus.service

# Give the daemon a moment to write the first state file
sleep 3

info "Starting nstatus-conky.service…"
if systemctl --user is-active --quiet nstatus.service; then
    systemctl --user restart nstatus-conky.service
    success "nstatus-conky.service started"
else
    warn "nstatus.service does not appear to be running yet."
    warn "Try: systemctl --user start nstatus-conky.service"
fi

info "Starting nstatus-toggle.service (mode/reconnect buttons)…"
# Clear any previous failed/start-limit-hit state so restart is never silently skipped.
systemctl --user reset-failed nstatus-toggle.service 2>/dev/null || true
systemctl --user restart nstatus-toggle.service
if systemctl --user is-active --quiet nstatus-toggle.service; then
    success "nstatus-toggle.service started"
else
    warn "nstatus-toggle.service failed to start."
    warn "Check: journalctl --user -u nstatus-toggle -n 20"
fi

# ── Done ─────────────────────────────────────────────────────────────────── #
echo
echo -e "${GREEN}${BOLD}╔══════════════════════════════════╗"
echo -e "║   Installation complete!         ║"
echo -e "╚══════════════════════════════════╝${RESET}"
echo
echo -e "  Config dir   : ${BOLD}${INSTALL_DIR}${RESET}"
echo -e "  Data dir     : ${BOLD}${DATA_DIR}${RESET}"
echo -e "  State file   : ${BOLD}${DATA_DIR}/state.json${RESET}"
echo -e "  Conky data   : ${BOLD}${DATA_DIR}/conky_data.txt${RESET}"
echo -e "  Logs         : ${BOLD}${DATA_DIR}/logs/nstatus.log${RESET}"
echo
echo -e "  View logs    : ${CYAN}journalctl --user -u nstatus -f${RESET}"
echo -e "  Daemon status: ${CYAN}systemctl --user status nstatus${RESET}"
echo -e "  Buttons log  : ${CYAN}journalctl --user -u nstatus-toggle -f${RESET}"
echo -e "  Edit config  : ${CYAN}${INSTALL_DIR}/config.yaml${RESET}"
echo

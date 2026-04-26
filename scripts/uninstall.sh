#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# NStatus uninstall.sh  — removes all installed files for the current user.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
GREEN='\033[0;32m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }

echo -e "\n${BOLD}NStatus Uninstaller${RESET}\n"

INSTALL_DIR="${HOME}/.config/nstatus"
DATA_DIR="${HOME}/.local/share/nstatus"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

# Stop and disable services
for svc in nstatus-conky nstatus; do
    if systemctl --user is-active --quiet "${svc}.service" 2>/dev/null; then
        info "Stopping ${svc}.service…"
        systemctl --user stop "${svc}.service"
    fi
    if systemctl --user is-enabled --quiet "${svc}.service" 2>/dev/null; then
        info "Disabling ${svc}.service…"
        systemctl --user disable "${svc}.service"
    fi
    if [ -f "${SYSTEMD_USER_DIR}/${svc}.service" ]; then
        rm -f "${SYSTEMD_USER_DIR}/${svc}.service"
        success "Removed ${svc}.service unit"
    fi
done

systemctl --user daemon-reload

# Remove install dir (config + src)
if [ -d "${INSTALL_DIR}" ]; then
    echo -e "\n${YELLOW}The following directory will be deleted:${RESET}"
    echo "  ${INSTALL_DIR}"
    read -r -p "Remove config and source files? [y/N] " ans
    if [[ "${ans}" =~ ^[Yy]$ ]]; then
        rm -rf "${INSTALL_DIR}"
        success "Removed ${INSTALL_DIR}"
    else
        warn "Skipped — ${INSTALL_DIR} kept"
    fi
fi

# Remove data dir (DB, logs, state files)
if [ -d "${DATA_DIR}" ]; then
    echo -e "\n${YELLOW}The following directory will be deleted:${RESET}"
    echo "  ${DATA_DIR}  (database, logs, state files)"
    read -r -p "Remove all data? [y/N] " ans
    if [[ "${ans}" =~ ^[Yy]$ ]]; then
        rm -rf "${DATA_DIR}"
        success "Removed ${DATA_DIR}"
    else
        warn "Skipped — ${DATA_DIR} kept"
    fi
fi

echo -e "\n${GREEN}${BOLD}NStatus uninstalled.${RESET}\n"

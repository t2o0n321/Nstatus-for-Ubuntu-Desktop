#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ipoe_reconfigure.sh
#
# Applies IPoE settings from ipoe.conf and reconnects — no interaction
# required.  Double-click this file in Nautilus to run it.
#
# Supports three methods (set IPOE_METHOD in ipoe.conf):
#   nmcli    — NetworkManager  (DHCP and static)
#   dhclient — classic DHCP client  (DHCP only)
#   raw      — bare ip commands  (static only, no NetworkManager)
#
# Requires: sudo privileges (asked once)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Re-launch inside a terminal if double-clicked from the file manager ──────
if [ ! -t 0 ]; then
    SELF="$(realpath "$0")"
    for TERM_EMU in gnome-terminal xterm konsole xfce4-terminal lxterminal; do
        if command -v "$TERM_EMU" &>/dev/null; then
            case "$TERM_EMU" in
                gnome-terminal) exec gnome-terminal -- bash "$SELF" ;;
                *)              exec "$TERM_EMU" -e "bash \"$SELF\"" ;;
            esac
        fi
    done
    zenity --error --text="No terminal emulator found.\nInstall gnome-terminal or xterm." 2>/dev/null || true
    exit 1
fi

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; echo; read -rp "Press Enter to close…"; exit 1; }
step()    { echo -e "\n${BOLD}$*${RESET}"; }

# ── Banner ───────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}╔══════════════════════════════════╗"
echo -e "║   IPoE Reconfigure               ║"
echo -e "╚══════════════════════════════════╝${RESET}\n"

# ── Load config ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$SCRIPT_DIR/ipoe.conf"

[ -f "$CONF" ] || die "Config file not found: $CONF"
source "$CONF"

[ -z "${IPOE_IFACE:-}"  ] && die "IPOE_IFACE is not set in ipoe.conf"
[ -z "${IPOE_MODE:-}"   ] && die "IPOE_MODE is not set in ipoe.conf"
[ -z "${IPOE_METHOD:-}" ] && die "IPOE_METHOD is not set in ipoe.conf"

# Validate mode / method combinations
if [ "$IPOE_MODE" = "static" ]; then
    [ -z "${IPOE_ADDRESS:-}" ] && die "IPOE_ADDRESS is required when IPOE_MODE=static"
    [ -z "${IPOE_GATEWAY:-}" ] && die "IPOE_GATEWAY is required when IPOE_MODE=static"
    [ "$IPOE_METHOD" = "dhclient" ] && die "IPOE_METHOD=dhclient cannot be used with IPOE_MODE=static"
fi
if [ "$IPOE_MODE" = "dhcp" ]; then
    [ "$IPOE_METHOD" = "raw" ] && die "IPOE_METHOD=raw cannot be used with IPOE_MODE=dhcp"
fi

# ── Check interface ──────────────────────────────────────────────────────────
if ! ip link show "$IPOE_IFACE" &>/dev/null; then
    warn "Interface '$IPOE_IFACE' not found.  Available interfaces:"
    ip -o link show | awk -F': ' '{print "  " $2}'
    die "Set IPOE_IFACE to the correct interface in ipoe.conf."
fi

# ── Show current status ──────────────────────────────────────────────────────
step "Current status of $IPOE_IFACE"
CURRENT_IP=$(ip addr show "$IPOE_IFACE" | awk '/inet /{print $2}' | head -1)
if [ -n "$CURRENT_IP" ]; then
    info "Current IP : $CURRENT_IP"
    GW=$(ip route | awk "/default.*$IPOE_IFACE/{print \$3}" | head -1)
    [ -n "$GW" ] && info "Gateway    : $GW"
else
    info "No IP address assigned to $IPOE_IFACE"
fi

# ── Acquire sudo ─────────────────────────────────────────────────────────────
step "Requesting elevated privileges"
sudo -v 2>/dev/null || die "sudo authentication failed."
success "sudo OK"

# ═════════════════════════════════════════════════════════════════════════════
# Method: nmcli
# ═════════════════════════════════════════════════════════════════════════════
if [ "$IPOE_METHOD" = "nmcli" ]; then

    NM_CONN="${IPOE_NM_CONNECTION:-}"
    [ -z "$NM_CONN" ] && die "IPOE_NM_CONNECTION is not set in ipoe.conf"
    command -v nmcli &>/dev/null || die "nmcli not found — is NetworkManager installed?"

    if ! nmcli connection show "$NM_CONN" &>/dev/null; then
        warn "Connection '$NM_CONN' not found.  Existing connections:"
        nmcli -t -f NAME connection show | sed 's/^/  /'
        die "Set IPOE_NM_CONNECTION to the correct name in ipoe.conf."
    fi

    step "Applying $IPOE_MODE settings via NetworkManager"

    if [ "$IPOE_MODE" = "dhcp" ]; then
        sudo nmcli connection modify "$NM_CONN" \
            ipv4.method        auto \
            ipv4.addresses     "" \
            ipv4.gateway       "" \
            ipv4.dns           ""
        success "Connection set to DHCP"

    else  # static
        DNS_LIST="${IPOE_DNS1:-}${IPOE_DNS2:+,${IPOE_DNS2}}"
        sudo nmcli connection modify "$NM_CONN" \
            ipv4.method    manual \
            ipv4.addresses "$IPOE_ADDRESS" \
            ipv4.gateway   "$IPOE_GATEWAY" \
            ipv4.dns       "$DNS_LIST"
        info "Address : $IPOE_ADDRESS"
        info "Gateway : $IPOE_GATEWAY"
        info "DNS     : $DNS_LIST"
        success "Connection set to static IP"
    fi

    step "Reconnecting"
    sudo nmcli connection down "$NM_CONN" 2>/dev/null || true
    sleep 1
    sudo nmcli connection up "$NM_CONN" || die "nmcli connection up failed"

    sleep 2
    NEW_IP=$(ip addr show "$IPOE_IFACE" | awk '/inet /{print $2}' | head -1)
    success "Interface is UP  —  IP: ${NEW_IP:-unknown}"

# ═════════════════════════════════════════════════════════════════════════════
# Method: dhclient  (DHCP only)
# ═════════════════════════════════════════════════════════════════════════════
elif [ "$IPOE_METHOD" = "dhclient" ]; then

    command -v dhclient &>/dev/null || die "dhclient not found — install isc-dhcp-client"

    step "Releasing current DHCP lease on $IPOE_IFACE"
    sudo dhclient -r "$IPOE_IFACE" 2>/dev/null || true
    sleep 1
    success "Lease released"

    step "Requesting new DHCP lease on $IPOE_IFACE"
    sudo dhclient "$IPOE_IFACE" || die "dhclient failed — check /var/log/syslog"

    sleep 2
    NEW_IP=$(ip addr show "$IPOE_IFACE" | awk '/inet /{print $2}' | head -1)
    NEW_GW=$(ip route | awk "/default.*$IPOE_IFACE/{print \$3}" | head -1)
    success "Interface is UP"
    [ -n "$NEW_IP" ] && info "New IP      : $NEW_IP"
    [ -n "$NEW_GW" ] && info "New gateway : $NEW_GW"

# ═════════════════════════════════════════════════════════════════════════════
# Method: raw  (static only — bare ip commands, no NetworkManager)
# ═════════════════════════════════════════════════════════════════════════════
elif [ "$IPOE_METHOD" = "raw" ]; then

    step "Flushing current IP configuration on $IPOE_IFACE"
    sudo ip addr flush dev "$IPOE_IFACE"
    sudo ip route del default dev "$IPOE_IFACE" 2>/dev/null || true
    success "Flushed"

    step "Applying static IP configuration"
    sudo ip addr add "$IPOE_ADDRESS" dev "$IPOE_IFACE"
    sudo ip link set "$IPOE_IFACE" up
    sudo ip route add default via "$IPOE_GATEWAY" dev "$IPOE_IFACE"
    info "Address : $IPOE_ADDRESS"
    info "Gateway : $IPOE_GATEWAY"
    success "IP and route configured"

    # DNS via /etc/resolv.conf
    if [ -n "${IPOE_DNS1:-}" ]; then
        step "Writing DNS to /etc/resolv.conf"
        {
            echo "# Generated by ipoe_reconfigure.sh — $(date)"
            echo "nameserver $IPOE_DNS1"
            [ -n "${IPOE_DNS2:-}" ] && echo "nameserver $IPOE_DNS2"
        } | sudo tee /etc/resolv.conf > /dev/null
        success "/etc/resolv.conf updated"
        warn "Note: NetworkManager or systemd-resolved may overwrite /etc/resolv.conf on next restart."
    fi

    NEW_IP=$(ip addr show "$IPOE_IFACE" | awk '/inet /{print $2}' | head -1)
    success "Interface is UP  —  IP: ${NEW_IP:-unknown}"

else
    die "Unknown IPOE_METHOD '$IPOE_METHOD' — must be 'nmcli', 'dhclient', or 'raw'"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo
echo -e "${GREEN}${BOLD}╔══════════════════════════════════╗"
echo -e "║   Reconfiguration complete!      ║"
echo -e "╚══════════════════════════════════╝${RESET}"
echo
echo -e "  Config  : ${DIM}$CONF${RESET}"
echo -e "  Mode    : ${DIM}$IPOE_MODE${RESET}"
echo -e "  Method  : ${DIM}$IPOE_METHOD${RESET}"
echo -e "  Iface   : ${DIM}$IPOE_IFACE${RESET}"
echo

read -rp "Press Enter to close…"

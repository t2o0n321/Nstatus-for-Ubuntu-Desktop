#!/usr/bin/env bash
# NStatus — restart helper
# Usage: bash scripts/restart.sh [all|daemon|conky|button]
#
# "all"    — restart daemon + Conky + toggle button  (default)
# "daemon" — restart only the nstatus daemon
# "conky"  — restart only the Conky widget
# "button" — restart only the GTK toggle button
set -euo pipefail

TARGET="${1:-all}"

# Auto-detect DISPLAY and XAUTHORITY from the live GNOME/X session.
# Required when called from a shell with no display (Claude Code, SSH, systemd).
# Iterates all matching PIDs because the first gnome-shell process may be a
# setup helper that has no display vars — the real session is a later PID.
if [[ -z "${DISPLAY:-}" || -z "${XAUTHORITY:-}" ]]; then
    for _proc in gnome-session gnome-shell; do
        while IFS= read -r _pid; do
            _env=$(cat /proc/"$_pid"/environ 2>/dev/null | tr '\0' '\n' || true)
            _d=$(printf '%s' "$_env"  | grep '^DISPLAY='    | cut -d= -f2- | head -1 || true)
            _x=$(printf '%s' "$_env"  | grep '^XAUTHORITY=' | cut -d= -f2- | head -1 || true)
            if [[ -n "$_d" && -n "$_x" ]]; then
                [[ -z "${DISPLAY:-}" ]]    && DISPLAY="$_d"
                [[ -z "${XAUTHORITY:-}" ]] && XAUTHORITY="$_x"
                break 2
            fi
        done < <(pgrep -u "$USER" "$_proc" 2>/dev/null || true)
    done
fi
DISPLAY="${DISPLAY:-:0}"
export DISPLAY
[[ -n "${XAUTHORITY:-}" ]] && export XAUTHORITY
export GDK_BACKEND="${GDK_BACKEND:-x11}"

_restart_daemon() {
    echo "  → restarting nstatus daemon"
    systemctl --user restart nstatus.service
}

_restart_conky() {
    echo "  → restarting Conky widget"
    systemctl --user restart nstatus-conky.service
}

_restart_button() {
    echo "  → restarting toggle button (DISPLAY=$DISPLAY)"
    pkill -f toggle_button.py 2>/dev/null || true
    sleep 0.5
    nohup python3 ~/.config/nstatus/src/toggle_button.py > /tmp/nstatus-toggle.log 2>&1 &
    echo "     PID $!  (log: /tmp/nstatus-toggle.log)"
}

echo "NStatus restart: $TARGET"
case "$TARGET" in
    daemon) _restart_daemon ;;
    conky)  _restart_conky  ;;
    button) _restart_button ;;
    all)
        _restart_daemon
        _restart_conky
        _restart_button
        ;;
    *)
        echo "Usage: $0 [all|daemon|conky|button]"
        exit 1
        ;;
esac
echo "Done."

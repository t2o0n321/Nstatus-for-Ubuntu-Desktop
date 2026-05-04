# NStatus — Desktop Network Monitor for Ubuntu

An always-on network monitoring widget for Ubuntu Desktop.  
Displays real-time QoS metrics, throughput, ISP/IP intelligence, and optional Cloudflare service health in a transparent Conky overlay — without ever blocking your desktop.

---

## What it looks like

```
  ╔══════════════════════════╗
  ║  NStatus Network Monitor ║
  ╚══════════════════════════╝
  [● Full]  [○ Simple]           ← clickable GTK toggle button

Quality  Excellent  (98/100)
Updated  2026-05-03 23:07:05
Target   8.8.8.8

── QoS Metrics ─────────────
  Latency (avg)  4.2 ms
    min/max      2.1  /  9.8 ms
  Jitter         0.9 ms
  Packet Loss    0.0%
  DNS Latency    12 ms  (google.com)

── LAN / Gateway ────────────
  Gateway IP    192.168.1.1  (eth0)
  LAN Latency   0.4 ms
  LAN Loss      0.0%
  WAN Type      PPPoE  (MTU 1492)

── Throughput ───────────────
  Download      94.2 Mbps
  Upload        42.1 Mbps
  Last tested   2026-05-03 22:50

── History (avg) ────────────
  1 h   RTT 4.4 ms  loss 0.0%
  24 h  RTT 5.1 ms  loss 0.0%

── Network Identity ─────────
  Public IP     203.0.113.42
  ISP           Example ISP Ltd
  ASN           AS12345
  Location      Tokyo, Japan
  IPv6          ✓ Available  (8 ms)
  IP Type       LIKELY_STATIC
                Stable 9d, dynamic in 5d

── Cloudflare Services ──────
  My Site ☁CF
    Status   ✓ 200 OK  [HIT]
    TTFB     18 ms  Total 45 ms
    TLS      12 ms  PoP NRT (Tokyo)
    Uptime   100.0% (24 h)
────────────────────────────
```

Clicking `[● Full]` / `[○ Simple]` switches to a minimal view showing only Quality, IP, and Update time.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          nstatus daemon                             │
│  (asyncio process — systemd user service)                           │
│                                                                     │
│  fast_loop (10 s)     slow_loop (10 min)    ip_loop (5 min)         │
│  ┌──────────────┐     ┌──────────────────┐  ┌───────────────────┐   │
│  │ping_collector│     │throughput_       │  │ip_collector       │   │
│  │dns_collector │     │collector         │  │→ public IP/ISP/ASN│   │
│  │gateway_      │     │→ DL/UL Mbps      │  │                   │   │
│  │collector     │     └──────────────────┘  │ipv6 check         │   │
│  │→ RTT/jitter  │                           └───────────────────┘   │
│  │  loss/DNS/GW │     cloudflare_loop (60 s)  wan_loop (30 min)     │
│  └──────┬───────┘     ┌──────────────────┐  ┌───────────────────┐   │
│         │             │cloudflare_       │  │wan_type_collector │   │
│         │             │collector         │  │→ tracepath PMTU   │   │
│         │             │→ status/TTFB/PoP │  │  PPPoE / IPoE     │   │
│         │             └──────────────────┘  └───────────────────┘   │
│         └─────────────────────┬───────────────────────────────      │
│                               │                                     │
│                    ┌──────────▼──────────┐                          │
│                    │      Analyzer       │                          │
│                    │  stats.py           │  RTT/jitter/loss stats   │
│                    │  ip_tracker.py      │  SQLite IP history       │
│                    │  quality_score.py   │  composite 0-100 score   │
│                    └──────────┬──────────┘                          │
│                               │                                     │
│              ┌────────────────┴───────────────┐                     │
│       ┌──────▼──────┐               ┌──────────▼──────┐             │
│       │  state.json │               │ conky_data.txt  │             │
│       │  (all data) │               │  (Conky markup) │             │
│       └─────────────┘               └──────────┬──────┘             │
└─────────────────────────────────────────────────┼───────────────────┘
                                                  │ atomic write (os.replace)
                                                  ▼
                                       ┌──────────────────┐
                                       │   Conky Widget   │
                                       │  (execpi 2 cat)  │
                                       └──────────────────┘
                                                  ▲
                                       ┌──────────┴──────────┐
                                       │  GTK Toggle Button  │
                                       │  (toggle_button.py) │
                                       │  GNOME autostart    │
                                       └─────────────────────┘
```

### Module breakdown

| Module | Responsibility |
|---|---|
| `src/main.py` | Async daemon, signal handling, loop orchestration |
| `src/config.py` | YAML config loader with deep-merge and `~` expansion |
| `src/collector/ping_collector.py` | Async `ping` subprocess → RTT / jitter / loss |
| `src/collector/dns_collector.py` | DNS resolution latency via `dig` |
| `src/collector/gateway_collector.py` | LAN gateway ping + IPv6 reachability |
| `src/collector/ip_collector.py` | `curl ipinfo.io` (with ip-api.com fallback) |
| `src/collector/throughput_collector.py` | `speedtest-cli --json` or `iperf3 -J` |
| `src/collector/cloudflare_collector.py` | HTTP probe of Cloudflare-served endpoints |
| `src/collector/wan_type_collector.py` | WAN type detection via `tracepath` Path-MTU (PPPoE / IPoE) |
| `src/analyzer/stats.py` | RTT avg/min/max, jitter (RFC 3550), std-dev |
| `src/analyzer/ip_tracker.py` | IP-type heuristic using SQLite history |
| `src/analyzer/quality_score.py` | Composite 0–100 quality score |
| `src/storage/database.py` | SQLite WAL-mode store with per-table retention |
| `src/storage/state_writer.py` | Atomic JSON + Conky-markup writers; Simple Mode aware |
| `src/toggle_button.py` | GTK overlay button for Full / Simple mode toggle |
| `scripts/regen_conky.sh` | One-shot regeneration of `conky_data.txt` from `state.json` |

---

## Project structure

```
nstatus/
├── src/
│   ├── main.py
│   ├── config.py
│   ├── toggle_button.py          # GTK mode-toggle overlay
│   ├── collector/
│   │   ├── ping_collector.py
│   │   ├── dns_collector.py
│   │   ├── gateway_collector.py
│   │   ├── ip_collector.py
│   │   ├── throughput_collector.py
│   │   ├── cloudflare_collector.py
│   │   └── wan_type_collector.py
│   ├── analyzer/
│   │   ├── stats.py
│   │   ├── ip_tracker.py
│   │   └── quality_score.py
│   └── storage/
│       ├── database.py
│       └── state_writer.py
├── config/
│   └── config.yaml               # annotated default config
├── conky/
│   └── nstatus.conf              # Conky widget config
├── scripts/
│   ├── install.sh
│   ├── uninstall.sh
│   ├── regen_conky.sh            # force-regenerate conky_data.txt
│   ├── pppoe/
│   │   ├── pppoe.conf            # edit with your ISP credentials
│   │   └── pppoe_reconfigure.sh  # double-click to apply PPPoE settings
│   └── ipoe/
│       ├── ipoe.conf             # edit with your IP / interface settings
│       └── ipoe_reconfigure.sh   # double-click to apply IPoE settings
├── systemd/
│   ├── nstatus.service           # daemon
│   ├── nstatus-conky.service     # Conky widget
│   └── nstatus-toggle.service    # (reference only — use autostart instead)
├── requirements.txt
└── README.md
```

Runtime files (created on first run, git-ignored):

```
~/.config/nstatus/          # installed source + config
~/.local/share/nstatus/
├── state.json              # latest full state
├── conky_data.txt          # Conky markup (written every ~10 s)
├── nstatus.db              # SQLite metrics database
├── simple_mode             # flag file: exists → Simple Mode active
├── logs/
│   └── nstatus.log
└── venv/                   # Python virtual environment
```

---

## Installation (Ubuntu 22.04 / 24.04)

### 1. System dependencies

```bash
sudo apt update
sudo apt install -y \
    python3 python3-pip python3-venv \
    conky-all \
    curl \
    iputils-ping \
    dnsutils \
    iproute2 \
    python3-gi python3-gi-cairo gir1.2-gtk-3.0
    # python3-gi is required for the mode-toggle button
    # iperf3 is optional — only needed if throughput.method=iperf3
```

### 2. Clone and install

```bash
git clone https://github.com/t2o0n321/nstatus-for-ubuntu-desktop.git
cd nstatus-for-ubuntu-desktop
bash scripts/install.sh
```

The installer:
- Checks all required commands
- Copies `src/`, `conky/`, and `config.yaml` to `~/.config/nstatus/`
- Creates `~/.local/share/nstatus/` with a Python venv
- Installs `pyyaml` and `speedtest-cli` into the venv
- Installs and starts `nstatus.service` and `nstatus-conky.service`

### 3. Set up the mode-toggle button

The toggle button is a separate GTK overlay — it cannot be managed by systemd (no display session available) so it uses GNOME autostart instead.

```bash
# Copy the scripts directory (if not already done by install.sh)
cp scripts/regen_conky.sh ~/.config/nstatus/scripts/regen_conky.sh
chmod +x ~/.config/nstatus/scripts/regen_conky.sh

# Install the GNOME autostart entry
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/nstatus-toggle.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=NStatus Toggle Button
Comment=Transparent mode-toggle overlay for the NStatus Conky widget
Exec=env GDK_BACKEND=x11 /usr/bin/python3 /home/$USER/.config/nstatus/src/toggle_button.py
Icon=network-transmit-receive
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF

# Start it now without logging out
GDK_BACKEND=x11 DISPLAY=:0 nohup python3 ~/.config/nstatus/src/toggle_button.py &
```

> **Wayland note:** The toggle button requires `GDK_BACKEND=x11` to force XWayland.  
> This is already set in the autostart `Exec` line and the launch command above.

### 4. Customise

```bash
nano ~/.config/nstatus/config.yaml

# Apply — always restart the daemon after config changes
systemctl --user restart nstatus.service
```

### 5. Adjust widget position

Edit `~/.config/nstatus/conky/nstatus.conf`:

```lua
alignment = 'top_right',   -- top_right | top_left | bottom_right | bottom_left
gap_x     = 20,            -- horizontal gap from screen edge (px)
gap_y     = 50,            -- vertical gap from screen edge (px)
```

Then restart Conky and the toggle button together (so the button re-reads Conky's window position):

```bash
systemctl --user restart nstatus-conky.service
pkill -f toggle_button.py
GDK_BACKEND=x11 DISPLAY=:0 nohup python3 ~/.config/nstatus/src/toggle_button.py &
```

---

## Configuration reference (`config.yaml`)

### Network / timing

```yaml
network:
  ping_target: "8.8.8.8"            # WAN latency target
  ping_alt_target: "1.1.1.1"        # fallback target
  ping_count: 10                    # packets per cycle
  fast_interval_seconds: 10         # ping + DNS + gateway
  slow_interval_seconds: 600        # throughput test (10 min)
  ip_check_interval_seconds: 300    # public IP refresh (5 min)
  dns_target: "google.com"          # DNS latency probe host
```

### Throughput

```yaml
throughput:
  method: "speedtest"     # "speedtest" or "iperf3"
  iperf3_server: ""       # required when method=iperf3
  timeout_seconds: 120
```

### IP tracking heuristic

```yaml
ip_tracking:
  history_days: 30
  static_threshold_days: 7      # stable this long → LIKELY_STATIC
  dynamic_change_threshold: 3   # this many changes in 30 d → DYNAMIC
```

| Condition | Label |
|---|---|
| ≥ 3 distinct IPs in 30 days | **DYNAMIC** |
| Current IP stable ≥ 7 days | **LIKELY_STATIC** |
| Stable but below threshold | **DYNAMIC** (conservative) |
| Insufficient history | **UNCERTAIN** |

### WAN type detection

No configuration needed — the daemon runs `tracepath` automatically.

| Display | Meaning |
|---|---|
| `PPPoE  (MTU 1492)` | Path MTU dropped to 1492 at the gateway — 8-byte PPPoE/PPP overhead |
| `IPoE   (MTU 1500)` | Path MTU stayed at 1500 throughout — standard Ethernet |
| `Checking…` | First `tracepath` run not yet complete (takes up to ~20 s on startup) |
| `UNKNOWN` | `tracepath` timed out, returned no PMTU data, or is not installed |

The check runs once on startup then every **30 minutes** — WAN type almost never changes mid-session.  Requires `tracepath` from the `iputils-tracepath` package (usually already present via `iputils-ping`).

### Cloudflare monitoring

```yaml
cloudflare:
  endpoints:
    - name: "My Site"
      url: "https://example.com"
    - name: "API"
      url: "https://api.example.com/health"
  check_interval_seconds: 60
  timeout_seconds: 10
```

Leave `endpoints: []` to disable the Cloudflare section entirely.  
The widget shows per-endpoint: HTTP status, cache status (HIT/MISS/DYNAMIC), TTFB, TLS handshake, PoP location, and 24-hour uptime %.

### Data retention

```yaml
retention:
  fast_hours: 48           # metrics_fast table window
  dns_hours: 48
  cloudflare_days: 7
  slow_days: 90
  ip_history_days: 365
  max_fast_rows: 20000     # hard row caps (oldest deleted first)
  max_dns_rows: 20000
  max_cloudflare_rows: 15000
  max_slow_rows: 500
  cleanup_interval_hours: 1
  vacuum_interval_days: 7
```

### Logging

```yaml
logging:
  level: "INFO"       # DEBUG | INFO | WARNING | ERROR
  max_bytes: 10485760
  backup_count: 3
```

---

## Simple Mode

Clicking `[● Full]` / `[○ Simple]` in the widget toggles between views.

**Full mode** — all sections (QoS, LAN, Throughput, History, Identity, Cloudflare).  
**Simple mode** — Quality score, Updated time, Public IP, and IP Type only.

The state is persisted as a flag file: `~/.local/share/nstatus/simple_mode`.  
Toggle from the terminal:

```bash
# Enable Simple Mode
touch ~/.local/share/nstatus/simple_mode
bash ~/.config/nstatus/scripts/regen_conky.sh

# Disable Simple Mode
rm -f ~/.local/share/nstatus/simple_mode
bash ~/.config/nstatus/scripts/regen_conky.sh
```

---

## Operations

### Service management

```bash
# Status of all three components
systemctl --user status nstatus nstatus-conky
pgrep -a -f toggle_button.py

# Follow daemon logs live
journalctl --user -u nstatus -f

# Follow Conky logs
journalctl --user -u nstatus-conky -f

# Restart after config change
systemctl --user restart nstatus.service

# Restart everything
systemctl --user restart nstatus.service nstatus-conky.service
pkill -f toggle_button.py
GDK_BACKEND=x11 DISPLAY=:0 nohup python3 ~/.config/nstatus/src/toggle_button.py &

# Stop everything
systemctl --user stop nstatus.service nstatus-conky.service
pkill -f toggle_button.py

# Disable autostart
systemctl --user disable nstatus.service nstatus-conky.service
# Also remove ~/.config/autostart/nstatus-toggle.desktop
```

### Force-regenerate the display

Rewrites `conky_data.txt` immediately from the current `state.json` — useful after toggling Simple Mode from the terminal or after editing state_writer.py:

```bash
bash ~/.config/nstatus/scripts/regen_conky.sh
```

### Inspect live data

```bash
# Pretty-print the current state
python3 -m json.tool ~/.local/share/nstatus/state.json

# Watch the raw Conky markup update
watch -n 2 cat ~/.local/share/nstatus/conky_data.txt

# Check current mode
ls ~/.local/share/nstatus/simple_mode 2>/dev/null && echo "Simple" || echo "Full"
```

### Query the database

```bash
# Recent fast metrics (RTT, jitter, loss)
sqlite3 ~/.local/share/nstatus/nstatus.db \
  "SELECT datetime(timestamp,'localtime') ts,
          round(rtt_avg,1) rtt, round(jitter,1) jitter,
          round(packet_loss,1) loss
   FROM metrics_fast ORDER BY ts DESC LIMIT 20;"

# Throughput history
sqlite3 ~/.local/share/nstatus/nstatus.db \
  "SELECT datetime(timestamp,'localtime') ts,
          round(download_mbps,1) dl, round(upload_mbps,1) ul
   FROM metrics_slow ORDER BY ts DESC LIMIT 10;"

# IP change history
sqlite3 ~/.local/share/nstatus/nstatus.db \
  "SELECT datetime(first_seen,'localtime') first,
          datetime(last_seen,'localtime') last, ip_address
   FROM ip_history ORDER BY first_seen DESC LIMIT 20;"

# Table sizes
sqlite3 ~/.local/share/nstatus/nstatus.db \
  "SELECT name, COUNT(*) n FROM
     (SELECT 'fast' name, rowid FROM metrics_fast
      UNION ALL SELECT 'slow', rowid FROM metrics_slow
      UNION ALL SELECT 'dns', rowid FROM metrics_dns
      UNION ALL SELECT 'cf', rowid FROM metrics_cloudflare)
   GROUP BY name;"
```

---

## Debugging

### Widget not appearing at all

```bash
# 1. Is the daemon running?
systemctl --user status nstatus

# 2. Did it write the data file?
ls -la ~/.local/share/nstatus/conky_data.txt

# 3. Is Conky running?
systemctl --user status nstatus-conky
pgrep -a conky

# 4. Test Conky manually (Ctrl+C to stop)
conky --config ~/.config/nstatus/conky/nstatus.conf

# 5. Check daemon logs for errors
journalctl --user -u nstatus --since "5 minutes ago"
journalctl --user -u nstatus-conky --since "5 minutes ago"
```

### Toggle button not visible

```bash
# Is it running?
pgrep -a -f toggle_button.py

# Start it manually with output
DISPLAY=:0 GDK_BACKEND=x11 python3 ~/.config/nstatus/src/toggle_button.py

# Is its window registered in X11?
xwininfo -root -tree | grep toggle

# Missing python3-gi?
python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('OK')"
# If that fails: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0
```

### Toggle button overlaps Conky content

The button reads Conky's actual window position from `xwininfo` at startup and positions itself just below the title box.  If you change `gap_x`, `gap_y`, or `alignment` in `nstatus.conf`, restart the toggle button so it repositions:

```bash
pkill -f toggle_button.py
GDK_BACKEND=x11 DISPLAY=:0 nohup python3 ~/.config/nstatus/src/toggle_button.py &
```

### Widget shows stale data / "NStatus daemon not running"

Conky re-reads `conky_data.txt` every 2 seconds.  If the file is stale, the daemon has crashed:

```bash
systemctl --user restart nstatus.service
journalctl --user -u nstatus -n 50
```

### Clicking the button does nothing

Check that `regen_conky.sh` is executable and that the venv exists:

```bash
ls -la ~/.config/nstatus/scripts/regen_conky.sh
ls -la ~/.local/share/nstatus/venv/bin/python3
chmod +x ~/.config/nstatus/scripts/regen_conky.sh
```

### "speedtest-cli not found" or slow throughput tests

```bash
~/.local/share/nstatus/venv/bin/pip install speedtest-cli
systemctl --user restart nstatus.service
# Or switch to iperf3 in config.yaml: throughput.method=iperf3
```

### High CPU from speedtest

Increase `slow_interval_seconds` in `config.yaml` (e.g. `1800` for 30 min):

```bash
nano ~/.config/nstatus/config.yaml
systemctl --user restart nstatus.service
```

### Conky window appears on top of other windows

`own_window_type = 'desktop'` and `own_window_hints = 'undecorated,below,...'` are set by default and should keep it behind all normal windows.  If a compositor (Picom, KWin) ignores these hints, add `below` explicitly to the WM rules for the `conky` class.

### WAN Type shows "Checking…" or "UNKNOWN"

`Checking…` is normal for the first ~20 seconds after daemon start while `tracepath` runs.  
If it stays `UNKNOWN`:

```bash
# Is tracepath installed?
which tracepath || sudo apt install iputils-tracepath

# Test manually — look for a "pmtu 1492" or "pmtu 1500" line:
tracepath -n -m 8 8.8.8.8

# Check daemon log for wan_type errors:
journalctl --user -u nstatus --since "5 minutes ago" | grep wan
```

### Debug logging

Set `logging.level: "DEBUG"` in `config.yaml` and restart:

```bash
systemctl --user restart nstatus.service
journalctl --user -u nstatus -f
```

Debug output shows every collector result, every DB write, and each `conky_data.txt` regeneration.

---

## Editing source files

The running system reads from `~/.config/nstatus/`, not the cloned repo.  
After changing any file in the repo you must copy it to the install directory and restart the relevant component.

### Any Python source file (`src/`)

```bash
cp src/storage/state_writer.py  ~/.config/nstatus/src/storage/state_writer.py
cp src/main.py                  ~/.config/nstatus/src/main.py
cp src/toggle_button.py         ~/.config/nstatus/src/toggle_button.py
# ... and so on for whichever file you changed

# Always restart the daemon — it caches the module in memory and will
# overwrite conky_data.txt with the old format on the next cycle otherwise.
systemctl --user restart nstatus.service

# Optionally force an immediate redraw without waiting for the next cycle:
bash ~/.config/nstatus/scripts/regen_conky.sh
```

### Conky config (`conky/nstatus.conf`)

```bash
cp conky/nstatus.conf ~/.config/nstatus/conky/nstatus.conf
systemctl --user restart nstatus-conky.service
# Also restart the toggle button so it re-reads Conky's window position:
pkill -f toggle_button.py
GDK_BACKEND=x11 DISPLAY=:0 nohup python3 ~/.config/nstatus/src/toggle_button.py &
```

### Config file (`config/config.yaml`)

The installer only copies `config.yaml` once (it never overwrites an existing one).  
Edit the live config directly — no copy needed:

```bash
nano ~/.config/nstatus/config.yaml
systemctl --user restart nstatus.service
```

### Toggle button (`src/toggle_button.py`)

```bash
cp src/toggle_button.py ~/.config/nstatus/src/toggle_button.py
pkill -f toggle_button.py
GDK_BACKEND=x11 DISPLAY=:0 nohup python3 ~/.config/nstatus/src/toggle_button.py &
```

---

## Extending NStatus

Adding a new metric requires three small changes:

1. **Collector** — add `src/collector/my_metric.py` with `async def collect_my_metric() -> dict`
2. **Daemon** — call it in `_fast_loop` or `_slow_loop` in `src/main.py`; store result in `self._state["my_metrics"]`
3. **Display** — render `state["my_metrics"]` in `format_conky_text()` in `src/storage/state_writer.py`

See the **Editing source files** section above for the copy-then-restart workflow.

---

## Reconfiguration Scripts

These are **standalone utilities** — they have no dependency on the NStatus daemon.  
Double-click them in Nautilus (or run from a terminal) whenever you need to change your WAN connection settings.

### PPPoE (`scripts/pppoe/`)

For connections where Ubuntu dials PPPoE directly (modem in bridge mode).

**1. Edit `scripts/pppoe/pppoe.conf`:**

```bash
PPPOE_IFACE="eth0"              # interface wired to the modem
PPPOE_USERNAME="user@isp.net"   # ISP login
PPPOE_PASSWORD="yourpassword"
PPPOE_METHOD="pppd"             # "pppd" (bare metal) or "nmcli" (NetworkManager)

# pppd-specific
PPPOE_PEER_NAME="dsl-provider"  # peer file under /etc/ppp/peers/
PPPOE_EXTRA_OPTS=""             # extra pppd options (usually leave blank)

# nmcli-specific
PPPOE_NM_CONNECTION="DSL Connection 1"
```

**2. Double-click `pppoe_reconfigure.sh`** (or run it in a terminal):

```bash
bash scripts/pppoe/pppoe_reconfigure.sh
```

What it does:

| Method | Actions |
|---|---|
| `pppd` | Writes `/etc/ppp/peers/<peer>`, updates `chap-secrets` + `pap-secrets`, runs `poff` then `pon`, waits up to 15 s for `ppp0` to appear |
| `nmcli` | Runs `nmcli connection modify` with new credentials, then `nmcli connection down/up` |

The script asks for your `sudo` password once and then runs unattended.

---

### IPoE (`scripts/ipoe/`)

For connections where the ISP assigns an IP directly on the Ethernet interface (DHCP or static) — no PPP dial-up.

**1. Edit `scripts/ipoe/ipoe.conf`:**

```bash
IPOE_IFACE="eth0"               # interface wired to the modem / ONT
IPOE_MODE="dhcp"                # "dhcp" or "static"
IPOE_METHOD="nmcli"             # "nmcli", "dhclient", or "raw"

# NetworkManager connection name (nmcli only)
IPOE_NM_CONNECTION="Wired connection 1"

# Static IP settings (IPOE_MODE=static only)
IPOE_ADDRESS="203.0.113.42/24"  # must include prefix length
IPOE_GATEWAY="203.0.113.1"
IPOE_DNS1="168.95.1.1"
IPOE_DNS2="8.8.8.8"
```

Valid method / mode combinations:

| `IPOE_METHOD` | `IPOE_MODE` | Notes |
|---|---|---|
| `nmcli` | `dhcp` | Sets `ipv4.method auto`, clears static settings |
| `nmcli` | `static` | Sets `ipv4.method manual` with address / gateway / DNS |
| `dhclient` | `dhcp` | Runs `dhclient -r` then `dhclient` — no NetworkManager needed |
| `raw` | `static` | `ip addr flush` + `ip addr add` + `ip route add default` + writes `/etc/resolv.conf` |

**2. Double-click `ipoe_reconfigure.sh`** (or run it in a terminal):

```bash
bash scripts/ipoe/ipoe_reconfigure.sh
```

> **Note:** When using `raw` + static, NetworkManager or systemd-resolved may overwrite `/etc/resolv.conf` on next restart.  Use `nmcli` + static if you want the settings to survive reboots.

---

## Uninstall

```bash
bash scripts/uninstall.sh
```

Interactively asks before removing the config and data directories.  
To also remove the toggle button autostart entry:

```bash
rm -f ~/.config/autostart/nstatus-toggle.desktop
pkill -f toggle_button.py
```

---

## Licence

MIT

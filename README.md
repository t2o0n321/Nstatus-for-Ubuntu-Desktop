# NStatus — Desktop Network Monitor for Ubuntu

A production-grade, always-on network monitoring widget for Ubuntu Desktop.
Displays real-time QoS metrics, throughput, and ISP/IP intelligence in a
Conky overlay — without ever blocking your desktop.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        nstatus daemon                           │
│  (long-running asyncio process — systemd user service)          │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │  fast_loop   │  │  slow_loop   │  │       ip_loop         │ │
│  │  (every 10s) │  │  (every 10m) │  │      (every 5m)       │ │
│  │              │  │              │  │                       │ │
│  │ ping_collector│ │ throughput_  │  │  ip_collector         │ │
│  │  → RTT       │  │  collector   │  │  → public IP          │ │
│  │  → jitter    │  │  speedtest / │  │  → ISP / ASN          │ │
│  │  → pkt loss  │  │  iperf3      │  │                       │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘ │
│         │                 │                     │              │
│         └─────────────────┴─────────────────────┘              │
│                           │                                    │
│                    ┌──────▼──────┐                             │
│                    │  Analyzer   │  (stats.py + ip_tracker.py) │
│                    │  ─ RTT avg  │                             │
│                    │  ─ jitter   │                             │
│                    │  ─ IP type  │  heuristic (SQLite history) │
│                    └──────┬──────┘                             │
│                           │                                    │
│              ┌────────────┴────────────┐                       │
│              │                         │                       │
│       ┌──────▼──────┐         ┌────────▼──────┐               │
│       │  state.json │         │conky_data.txt │               │
│       │  (full data)│         │(Conky markup) │               │
│       └─────────────┘         └───────┬───────┘               │
└───────────────────────────────────────┼───────────────────────┘
                                        │ atomic file write
                                        ▼
                              ┌─────────────────┐
                              │   Conky Widget  │
                              │  (${execpi 5    │
                              │   cat data.txt})│
                              └─────────────────┘
```

### Module breakdown

| Module | Responsibility |
|---|---|
| `src/config.py` | YAML config loader with deep-merge and path expansion |
| `src/collector/ping_collector.py` | Async `ping` subprocess → list of RTTs |
| `src/collector/ip_collector.py` | `curl ipinfo.io` (with ip-api.com fallback) |
| `src/collector/throughput_collector.py` | `speedtest-cli --json` or `iperf3 -J` |
| `src/analyzer/stats.py` | Pure functions: RTT avg/min/max, jitter (RFC 3550), std-dev |
| `src/analyzer/ip_tracker.py` | Heuristic classifier using SQLite IP history |
| `src/storage/database.py` | SQLite WAL-mode store for metrics + IP history |
| `src/storage/state_writer.py` | Atomic JSON + Conky-markup file writers |
| `src/main.py` | Async daemon, signal handling, loop orchestration |

### IP type heuristic

The tracker maintains a rolling 30-day history of observed public IPs.

| Condition | Label |
|---|---|
| ≥ 3 distinct IPs in 30 days | **DYNAMIC** |
| Current IP stable ≥ 7 days | **LIKELY_STATIC** |
| Changed but below thresholds | **DYNAMIC** (conservative) |
| Insufficient data | **UNCERTAIN** |

Both thresholds are tunable in `config.yaml`.

### Scheduling design

Two timescales avoid the cost of running a heavy test every few seconds:

- **Fast loop** (default 10 s) — `ping -c 10` costs ~2 s and is negligible
- **Slow loop** (default 600 s) — throughput test is deferred 45 s on startup and runs at most once per 10 min
- **IP loop** (default 300 s) — one `curl` call, effectively free

All loops are `asyncio` coroutines — none block the event loop. Each writes
to disk only via `os.replace()` (atomic rename), so Conky always reads a
complete file.

---

## Project structure

```
nstatus/
├── src/
│   ├── __init__.py
│   ├── main.py                        # daemon entry-point
│   ├── config.py                      # config loader
│   ├── collector/
│   │   ├── ping_collector.py          # RTT / jitter / loss
│   │   ├── ip_collector.py            # public IP + ISP
│   │   └── throughput_collector.py   # speedtest / iperf3
│   ├── analyzer/
│   │   ├── stats.py                   # ping statistics
│   │   └── ip_tracker.py             # IP type heuristic
│   └── storage/
│       ├── database.py                # SQLite layer
│       └── state_writer.py           # atomic file writers
├── config/
│   └── config.yaml                   # annotated default config
├── conky/
│   └── nstatus.conf                  # Conky widget config
├── scripts/
│   ├── install.sh                    # one-shot installer
│   └── uninstall.sh                  # clean uninstall
├── systemd/
│   ├── nstatus.service               # daemon service
│   └── nstatus-conky.service         # Conky service
├── data/                             # runtime data (git-ignored)
├── logs/                             # log files (git-ignored)
├── requirements.txt
└── README.md
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
    iperf3          # optional — only needed for iperf3 throughput method
```

### 2. Clone and install

```bash
git clone https://github.com/t2o0n321/nstatus-for-ubuntu-desktop.git
cd nstatus-for-ubuntu-desktop
bash scripts/install.sh
```

The installer:
- Creates `~/.config/nstatus/` (config + source)
- Creates `~/.local/share/nstatus/` (database, logs, state files)
- Builds a Python virtualenv at `~/.local/share/nstatus/venv`
- Installs `pyyaml` and `speedtest-cli` into the venv
- Installs and starts `nstatus.service` + `nstatus-conky.service`

### 3. Customise

```bash
# Edit config
nano ~/.config/nstatus/config.yaml

# Apply changes (restart the daemon)
systemctl --user restart nstatus.service
```

Key settings in `config.yaml`:

```yaml
network:
  ping_target: "8.8.8.8"        # change to your gateway for LAN latency
  fast_interval_seconds: 10      # ping frequency
  slow_interval_seconds: 600     # throughput test frequency

throughput:
  method: "speedtest"            # or "iperf3"
  iperf3_server: ""              # fill in if method=iperf3

ip_tracking:
  static_threshold_days: 7      # days without change → LIKELY_STATIC
  dynamic_change_threshold: 3   # n changes in 30d → DYNAMIC
```

### 4. Conky widget position

Edit `~/.config/nstatus/conky/nstatus.conf`:

```lua
alignment = 'top_right',   -- top_right | top_left | bottom_right | bottom_left
gap_x     = 20,            -- horizontal gap from edge (pixels)
gap_y     = 50,            -- vertical gap from edge (pixels)
```

Then restart Conky:
```bash
systemctl --user restart nstatus-conky.service
```

---

## Operations

### Service management

```bash
# Daemon status
systemctl --user status nstatus

# Follow logs
journalctl --user -u nstatus -f

# Restart after config change
systemctl --user restart nstatus.service

# Stop everything
systemctl --user stop nstatus.service nstatus-conky.service

# Disable autostart
systemctl --user disable nstatus.service nstatus-conky.service
```

### Inspect live data

```bash
# Pretty-print the current state
cat ~/.local/share/nstatus/state.json | python3 -m json.tool

# Watch the raw Conky markup
watch -n 5 cat ~/.local/share/nstatus/conky_data.txt
```

### Query the database

```bash
sqlite3 ~/.local/share/nstatus/nstatus.db \
  "SELECT datetime(timestamp,'localtime') as ts,
          round(rtt_avg,1) as rtt,
          round(jitter,1) as jitter,
          round(packet_loss,1) as loss
   FROM metrics_fast ORDER BY ts DESC LIMIT 20;"
```

---

## Uninstall

```bash
bash scripts/uninstall.sh
```

Interactively asks before deleting the config directory and the data directory.

---

## Common issues

### Widget not appearing

1. Check the daemon is running: `systemctl --user status nstatus`
2. Check the data file exists: `ls -la ~/.local/share/nstatus/`
3. Check Conky logs: `journalctl --user -u nstatus-conky`
4. Try starting Conky manually: `conky --config ~/.config/nstatus/conky/nstatus.conf`

### "speedtest-cli not found"

```bash
~/.local/share/nstatus/venv/bin/pip install speedtest-cli
systemctl --user restart nstatus.service
```

### Conky shows stale data

Conky re-reads the file every 5 s (`${execpi 5 cat ...}`).  If the daemon
crashed, restart it:

```bash
systemctl --user restart nstatus.service
```

### High CPU from speedtest

Increase `slow_interval_seconds` in `config.yaml` (e.g. `1800` = 30 min).

### Conky window is on top of other windows

Add `below` to `own_window_hints` in `conky/nstatus.conf` — it is already
there by default. If you use a compositor (Picom, Mutter), ensure desktop
windows are not raised on click.

---

## Extending NStatus

Adding a new metric requires three small changes:

1. **Collector** — add `src/collector/my_metric.py` with an `async def collect_my_metric()` function
2. **Daemon** — call the collector in `_fast_loop` or `_slow_loop` inside `src/main.py` and store the result in `self._state["my_metrics"]`
3. **Display** — render `state["my_metrics"]` in `format_conky_text()` inside `src/storage/state_writer.py`

No other files need to change.

---

## Licence

MIT

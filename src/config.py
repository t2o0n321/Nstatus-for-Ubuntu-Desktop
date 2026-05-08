"""Configuration loader with deep-merge and path expansion."""

import os
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# Built-in defaults — all user config is merged on top of these.
_DEFAULTS: dict = {
    "network": {
        "ping_target": "8.8.8.8",
        "ping_alt_target": "1.1.1.1",
        "ping_count": 10,
        "fast_interval_seconds": 10,
        "slow_interval_seconds": 600,
        "ip_check_interval_seconds": 300,
    },
    "ip_tracking": {
        "history_days": 30,
        "static_threshold_days": 7,
        "dynamic_change_threshold": 3,
    },
    "paths": {
        "data_dir":        "~/.local/share/nstatus",
        "log_dir":         "~/.local/share/nstatus/logs",
        "state_file":      "~/.local/share/nstatus/state.json",
        "db_file":         "~/.local/share/nstatus/nstatus.db",
        "conky_data_file": "~/.local/share/nstatus/conky_data.txt",
    },
    "throughput": {
        "method": "speedtest",
        "iperf3_server": "",
        "timeout_seconds": 120,
    },
    "logging": {
        "level": "INFO",
        "max_bytes": 10_485_760,
        "backup_count": 3,
    },
    "reconnect": {
        "method": "pppoe",   # "pppoe" or "ipoe"
    },
    "cloudflare": {
        # List of dicts: [{name: "My Site", url: "https://example.com"}, …]
        "endpoints": [],
        "check_interval_seconds": 60,
        "timeout_seconds": 10,
    },
    "retention": {
        # Per-table time-based retention
        "fast_hours":        48,    # metrics_fast  — 48 h covers 1 h + 24 h avg windows
        "dns_hours":         48,    # metrics_dns
        "cloudflare_days":    7,    # metrics_cloudflare
        "slow_days":         90,    # metrics_slow (throughput tests)
        "ip_history_days":  365,    # ip_history    — long window for IP-type heuristic
        # Hard row-count caps (applied after time-based cleanup)
        "max_fast_rows":    20000,
        "max_dns_rows":     20000,
        "max_cloudflare_rows": 15000,
        "max_slow_rows":     500,
        # How often to run cleanup (hours); use fractional hours (e.g. 0.5 = 30 min)
        "cleanup_interval_hours": 1,
        # Run VACUUM every this many days to reclaim freed pages
        "vacuum_interval_days":   7,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _expand_paths(data: dict) -> dict:
    data = dict(data)
    paths = dict(data.get("paths", {}))
    for k, v in paths.items():
        paths[k] = str(Path(v).expanduser())
    data["paths"] = paths
    return data


class Config:
    """
    Loads config.yaml (or uses built-in defaults) and exposes typed accessors.

    Resolution order (highest wins):
      1. NSTATUS_CONFIG env var → path to yaml file
      2. ~/.config/nstatus/config.yaml
      3. Built-in defaults
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        import copy
        self._data = copy.deepcopy(_DEFAULTS)

        if config_path and Path(config_path).exists():
            self._load_yaml(config_path)

        self._data = _expand_paths(self._data)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _load_yaml(self, path: str) -> None:
        if not _HAS_YAML:
            raise RuntimeError("PyYAML is not installed — cannot load config file.")
        with open(path) as fh:
            user = yaml.safe_load(fh) or {}
        self._data = _deep_merge(self._data, user)
        self._data = _expand_paths(self._data)

    def get(self, *keys: str, default: Any = None) -> Any:
        node = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    # ------------------------------------------------------------------ #
    # Typed properties                                                     #
    # ------------------------------------------------------------------ #

    @property
    def data_dir(self) -> Path:
        return Path(self.get("paths", "data_dir"))

    @property
    def log_dir(self) -> Path:
        return Path(self.get("paths", "log_dir"))

    @property
    def state_file(self) -> Path:
        return Path(self.get("paths", "state_file"))

    @property
    def db_file(self) -> Path:
        return Path(self.get("paths", "db_file"))

    @property
    def conky_data_file(self) -> Path:
        return Path(self.get("paths", "conky_data_file"))

    @property
    def ping_target(self) -> str:
        return self.get("network", "ping_target", default="8.8.8.8")

    @property
    def ping_alt_target(self) -> str:
        return self.get("network", "ping_alt_target", default="1.1.1.1")

    @property
    def ping_count(self) -> int:
        return int(self.get("network", "ping_count", default=10))

    @property
    def fast_interval(self) -> int:
        return int(self.get("network", "fast_interval_seconds", default=10))

    @property
    def slow_interval(self) -> int:
        return int(self.get("network", "slow_interval_seconds", default=600))

    @property
    def ip_check_interval(self) -> int:
        return int(self.get("network", "ip_check_interval_seconds", default=300))

    @property
    def throughput_method(self) -> str:
        return self.get("throughput", "method", default="speedtest")

    @property
    def reconnect_method(self) -> str:
        return self.get("reconnect", "method", default="pppoe")

    @property
    def throughput_timeout(self) -> int:
        return int(self.get("throughput", "timeout_seconds", default=120))

    @property
    def iperf3_server(self) -> str:
        return self.get("throughput", "iperf3_server", default="")

    # ------------------------------------------------------------------ #
    # Cloudflare                                                           #
    # ------------------------------------------------------------------ #

    @property
    def cloudflare_endpoints(self) -> list:
        """List of dicts with at least 'url' and optionally 'name'."""
        raw = self.get("cloudflare", "endpoints", default=[])
        # Validate: each entry must have a non-empty 'url'
        return [ep for ep in (raw or []) if isinstance(ep, dict) and ep.get("url")]

    @property
    def cloudflare_check_interval(self) -> int:
        return int(self.get("cloudflare", "check_interval_seconds", default=60))

    @property
    def cloudflare_timeout(self) -> int:
        return int(self.get("cloudflare", "timeout_seconds", default=10))

    # ------------------------------------------------------------------ #
    # Retention                                                            #
    # ------------------------------------------------------------------ #

    @property
    def retention_fast_hours(self) -> int:
        return int(self.get("retention", "fast_hours", default=48))

    @property
    def retention_dns_hours(self) -> int:
        return int(self.get("retention", "dns_hours", default=48))

    @property
    def retention_cloudflare_days(self) -> int:
        return int(self.get("retention", "cloudflare_days", default=7))

    @property
    def retention_slow_days(self) -> int:
        return int(self.get("retention", "slow_days", default=90))

    @property
    def retention_ip_history_days(self) -> int:
        return int(self.get("retention", "ip_history_days", default=365))

    @property
    def retention_max_fast_rows(self) -> int:
        return int(self.get("retention", "max_fast_rows", default=20000))

    @property
    def retention_max_dns_rows(self) -> int:
        return int(self.get("retention", "max_dns_rows", default=20000))

    @property
    def retention_max_cloudflare_rows(self) -> int:
        return int(self.get("retention", "max_cloudflare_rows", default=15000))

    @property
    def retention_max_slow_rows(self) -> int:
        return int(self.get("retention", "max_slow_rows", default=500))

    @property
    def retention_cleanup_interval_hours(self) -> float:
        return float(self.get("retention", "cleanup_interval_hours", default=1))

    @property
    def retention_vacuum_interval_days(self) -> int:
        return int(self.get("retention", "vacuum_interval_days", default=7))

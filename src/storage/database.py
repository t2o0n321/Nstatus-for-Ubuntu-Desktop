"""SQLite storage layer — metrics history and IP change tracking."""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
-- IP change history (one row per distinct public IP observed)
CREATE TABLE IF NOT EXISTS ip_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip          TEXT    NOT NULL,
    isp         TEXT    NOT NULL DEFAULT '',
    asn         TEXT    NOT NULL DEFAULT '',
    country     TEXT    NOT NULL DEFAULT '',
    city        TEXT    NOT NULL DEFAULT '',
    detected_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Fast (ping-based) metric snapshots
CREATE TABLE IF NOT EXISTS metrics_fast (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    rtt_avg     REAL,
    rtt_min     REAL,
    rtt_max     REAL,
    rtt_mdev    REAL,
    jitter      REAL,
    packet_loss REAL
);

-- Slow (throughput) metric snapshots
CREATE TABLE IF NOT EXISTS metrics_slow (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    download_mbps REAL,
    upload_mbps   REAL,
    method        TEXT NOT NULL DEFAULT ''
);

-- DNS latency snapshots
CREATE TABLE IF NOT EXISTS metrics_dns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    dns_ms      REAL,
    target      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ip_history_ts    ON ip_history(detected_at);
CREATE INDEX IF NOT EXISTS idx_metrics_fast_ts  ON metrics_fast(timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_slow_ts  ON metrics_slow(timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_dns_ts   ON metrics_dns(timestamp);
"""

# Migrations for users with an older DB (adds columns that may be missing).
_MIGRATIONS = [
    "ALTER TABLE metrics_fast ADD COLUMN rtt_mdev REAL",
    "CREATE TABLE IF NOT EXISTS metrics_dns ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),"
    "  dns_ms REAL,"
    "  target TEXT NOT NULL DEFAULT ''"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_metrics_dns_ts ON metrics_dns(timestamp)",
]


class Database:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._init_schema()
        self._run_migrations()

    # ------------------------------------------------------------------ #
    # Connection management                                                #
    # ------------------------------------------------------------------ #

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        conn = sqlite3.connect(self._path, timeout=10)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _run_migrations(self) -> None:
        """Apply any schema migrations that may be missing in an older DB."""
        for sql in _MIGRATIONS:
            try:
                conn = sqlite3.connect(self._path, timeout=10)
                conn.execute(sql)
                conn.commit()
                conn.close()
            except sqlite3.OperationalError:
                # Column/table already exists — expected on fresh installs
                pass

    # ------------------------------------------------------------------ #
    # IP history                                                           #
    # ------------------------------------------------------------------ #

    def get_latest_ip(self) -> Optional[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM ip_history ORDER BY detected_at DESC LIMIT 1"
            ).fetchone()

    def record_ip(
        self,
        ip: str,
        isp: str = "",
        asn: str = "",
        country: str = "",
        city: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO ip_history (ip, isp, asn, country, city, detected_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (ip, isp, asn, country, city, now),
            )
        logger.info("IP recorded: %s  ISP=%s  ASN=%s", ip, isp, asn)

    def get_ip_history(self, days: int = 30) -> List[sqlite3.Row]:
        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM ip_history"
                " WHERE detected_at >= ?"
                " ORDER BY detected_at ASC",
                (since,),
            ).fetchall()

    # ------------------------------------------------------------------ #
    # Fast metrics                                                         #
    # ------------------------------------------------------------------ #

    def record_fast_metric(
        self,
        rtt_avg: float,
        rtt_min: float,
        rtt_max: float,
        rtt_mdev: float,
        jitter: float,
        packet_loss: float,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO metrics_fast"
                "  (rtt_avg, rtt_min, rtt_max, rtt_mdev, jitter, packet_loss)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (rtt_avg, rtt_min, rtt_max, rtt_mdev, jitter, packet_loss),
            )

    # ------------------------------------------------------------------ #
    # Slow metrics                                                         #
    # ------------------------------------------------------------------ #

    def record_slow_metric(
        self,
        download_mbps: float,
        upload_mbps: float,
        method: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO metrics_slow (download_mbps, upload_mbps, method)"
                " VALUES (?, ?, ?)",
                (download_mbps, upload_mbps, method),
            )

    # ------------------------------------------------------------------ #
    # DNS metrics                                                          #
    # ------------------------------------------------------------------ #

    def record_dns_metric(self, dns_ms: float, target: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO metrics_dns (dns_ms, target) VALUES (?, ?)",
                (dns_ms, target),
            )

    # ------------------------------------------------------------------ #
    # Historical averages                                                  #
    # ------------------------------------------------------------------ #

    def get_fast_averages(self, hours: int) -> Dict[str, Any]:
        """Return avg RTT, jitter, and packet loss over the past *hours* hours."""
        since = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT"
                "  AVG(rtt_avg)     AS rtt_avg,"
                "  AVG(jitter)      AS jitter,"
                "  AVG(packet_loss) AS packet_loss,"
                "  COUNT(*)         AS samples"
                " FROM metrics_fast"
                " WHERE timestamp >= ?",
                (since,),
            ).fetchone()
        if row and row["samples"]:
            return {
                "rtt_avg":     round(row["rtt_avg"],     2),
                "jitter":      round(row["jitter"],      2),
                "packet_loss": round(row["packet_loss"], 2),
                "samples":     row["samples"],
            }
        return {}

    # ------------------------------------------------------------------ #
    # Maintenance                                                          #
    # ------------------------------------------------------------------ #

    def cleanup_old_records(self, days: int = 30) -> None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn() as conn:
            conn.execute("DELETE FROM metrics_fast WHERE timestamp < ?",   (cutoff,))
            conn.execute("DELETE FROM metrics_slow WHERE timestamp < ?",   (cutoff,))
            conn.execute("DELETE FROM metrics_dns  WHERE timestamp < ?",   (cutoff,))
            conn.execute("DELETE FROM ip_history   WHERE detected_at < ?", (cutoff,))
        logger.debug("Cleaned up records older than %d days", days)

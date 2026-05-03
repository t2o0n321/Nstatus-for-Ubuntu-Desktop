"""IP-type heuristic: classifies the public IP as DYNAMIC, LIKELY_STATIC, or UNCERTAIN."""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from src.storage.database import Database

logger = logging.getLogger(__name__)


class IPTracker:
    """
    Persists IP observations to SQLite and applies a multi-factor heuristic
    to infer whether the connection has a static or dynamic IP address.

    Decision table
    ──────────────
    actual IP transitions in history_days ≥ dynamic_change_threshold  →  DYNAMIC
    current IP stable for ≥ static_threshold_days                     →  LIKELY_STATIC
    any transitions but below both thresholds                         →  DYNAMIC (conservative)
    no transitions and below static threshold                         →  UNCERTAIN
    """

    def __init__(self, db: Database, config) -> None:
        self._db = db
        self._history_days: int      = config.get("ip_tracking", "history_days",            default=30)
        self._static_days: int       = config.get("ip_tracking", "static_threshold_days",   default=7)
        self._dynamic_threshold: int = config.get("ip_tracking", "dynamic_change_threshold", default=3)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def check_and_record(
        self,
        current_ip: str,
        isp: str = "",
        asn: str = "",
        country: str = "",
        city: str = "",
    ) -> bool:
        """
        Compare *current_ip* against the last-known IP.
        If different (or no history exists), record it in the database.

        Returns True if a change was detected.
        """
        latest = self._db.get_latest_ip()
        if latest is None or latest["ip"] != current_ip:
            self._db.record_ip(current_ip, isp, asn, country, city)
            return True
        return False

    def get_ip_type(self) -> Tuple[str, str]:
        """
        Returns (type_label, human_readable_reason).

        type_label is one of: "DYNAMIC" | "LIKELY_STATIC" | "UNCERTAIN"
        """
        history = self._db.get_ip_history(days=self._history_days)

        if not history:
            return "UNCERTAIN", "No history available yet"

        # ── Count actual IP transitions (A→B counts as 1, not distinct IPs) ─ #
        n_changes = 0
        prev_ip = history[0]["ip"]
        for row in history[1:]:
            if row["ip"] != prev_ip:
                n_changes += 1
            prev_ip = row["ip"]

        if n_changes >= self._dynamic_threshold:
            return (
                "DYNAMIC",
                f"IP changed {n_changes}× in last {self._history_days}d",
            )

        # ── Current-streak stability ────────────────────────────────────── #
        current_ip   = history[-1]["ip"]
        streak_start = self._streak_start(history, current_ip)
        now          = datetime.now(timezone.utc)
        stable_days  = (now - streak_start).days

        if stable_days >= self._static_days:
            return (
                "LIKELY_STATIC",
                f"Stable for {stable_days}d (threshold {self._static_days}d)",
            )

        if n_changes > 0:
            return (
                "DYNAMIC",
                f"Changed {n_changes}×, stable {stable_days}d now",
            )

        return (
            "UNCERTAIN",
            f"Stable {stable_days}d, need {self._static_days}d more",
        )

    def get_last_change_time(self) -> Optional[datetime]:
        """Return the UTC timestamp of the most recent IP change, or None."""
        history = self._db.get_ip_history(days=self._history_days)
        if len(history) < 2:
            return None

        last_change: Optional[datetime] = None
        prev_ip = history[0]["ip"]
        for row in history[1:]:
            if row["ip"] != prev_ip:
                last_change = self._parse_ts(row["detected_at"])
            prev_ip = row["ip"]
        return last_change

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _streak_start(history: list, ip: str) -> datetime:
        """Find the earliest timestamp in the current unbroken IP streak."""
        earliest = IPTracker._parse_ts(history[-1]["detected_at"])
        for row in reversed(history):
            if row["ip"] != ip:
                break
            earliest = IPTracker._parse_ts(row["detected_at"])
        return earliest

    @staticmethod
    def _parse_ts(ts_str: str) -> datetime:
        """Parse an ISO-8601 UTC timestamp stored by the database."""
        ts_str = ts_str.rstrip("Z")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

#!/usr/bin/env python3
"""
NStatus — Desktop Network Monitor daemon for Ubuntu.

Runs four independent async loops:
  fast_loop    — ping metrics every fast_interval seconds
  slow_loop    — throughput test every slow_interval seconds
  ip_loop      — public IP check every ip_check_interval seconds
  cleanup_loop — prune old DB records once per day

Writes results atomically to:
  ~/.local/share/nstatus/state.json       (machine-readable)
  ~/.local/share/nstatus/conky_data.txt   (Conky markup)
"""

import asyncio
import logging
import logging.handlers
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Allow both `python src/main.py` and `python -m src.main` invocations.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config
from src.storage.database import Database
from src.storage.state_writer import write_conky_data, write_state
from src.collector.ping_collector import collect_ping
from src.collector.ip_collector import collect_ip_info
from src.collector.throughput_collector import collect_throughput
from src.analyzer.stats import compute_ping_stats
from src.analyzer.ip_tracker import IPTracker


# ------------------------------------------------------------------ #
# Logging setup                                                        #
# ------------------------------------------------------------------ #

def _setup_logging(config: Config) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, config.get("logging", "level", default="INFO"), logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handlers: list = [logging.StreamHandler(sys.stdout)]

    log_file = config.log_dir / "nstatus.log"
    rh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=config.get("logging", "max_bytes", default=10_485_760),
        backupCount=config.get("logging", "backup_count", default=3),
    )
    rh.setFormatter(fmt)
    handlers.append(rh)

    root = logging.getLogger()
    root.setLevel(level)
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)

    # Silence noisy third-party loggers
    logging.getLogger("asyncio").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Daemon                                                               #
# ------------------------------------------------------------------ #

_INITIAL_STATE: Dict[str, Any] = {
    "updated_at":      "Starting…",
    "fast_metrics":    {},
    "slow_metrics":    {},
    "ip_info":         {},
    "ip_type":         "UNCERTAIN",
    "ip_type_reason":  "Not yet checked",
    "last_ip_change":  "Unknown",
}


class NStatusDaemon:
    def __init__(self, config: Config) -> None:
        self._cfg        = config
        self._db         = Database(config.db_file)
        self._ip_tracker = IPTracker(self._db, config)
        self._state: Dict[str, Any] = dict(_INITIAL_STATE)
        self._running    = True

    # ---------------------------------------------------------------- #
    # State I/O                                                          #
    # ---------------------------------------------------------------- #

    def _flush(self) -> None:
        try:
            write_state(self._cfg.state_file, self._state)
            write_conky_data(self._cfg.conky_data_file, self._state)
        except Exception as exc:
            logger.error("Failed to flush state: %s", exc)

    # ---------------------------------------------------------------- #
    # Loops                                                              #
    # ---------------------------------------------------------------- #

    async def _fast_loop(self) -> None:
        """Ping-based QoS metrics on a short interval."""
        interval = self._cfg.fast_interval
        logger.info("fast_loop started  (interval=%ds)", interval)
        while self._running:
            try:
                await self._collect_fast()
            except Exception as exc:
                logger.error("fast_loop unhandled error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def _slow_loop(self) -> None:
        """Throughput test on a long interval."""
        interval = self._cfg.slow_interval
        logger.info("slow_loop started  (interval=%ds)", interval)
        # Stagger first run to avoid overloading on startup
        await asyncio.sleep(45)
        while self._running:
            try:
                await self._collect_slow()
            except Exception as exc:
                logger.error("slow_loop unhandled error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def _ip_loop(self) -> None:
        """Public IP + ISP refresh on a medium interval."""
        interval = self._cfg.ip_check_interval
        logger.info("ip_loop started    (interval=%ds)", interval)
        while self._running:
            try:
                await self._collect_ip()
            except Exception as exc:
                logger.error("ip_loop unhandled error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def _cleanup_loop(self) -> None:
        """Daily database maintenance."""
        logger.info("cleanup_loop started (interval=86400s)")
        while self._running:
            try:
                self._db.cleanup_old_records(days=30)
                logger.info("DB cleanup complete")
            except Exception as exc:
                logger.error("cleanup_loop error: %s", exc)
            await asyncio.sleep(86_400)

    # ---------------------------------------------------------------- #
    # Collection tasks                                                   #
    # ---------------------------------------------------------------- #

    async def _collect_fast(self) -> None:
        target = self._cfg.ping_target
        count  = self._cfg.ping_count

        rtts, sent = await collect_ping(target, count=count)

        # Fallback to alt target on total failure
        if not rtts and sent == count:
            alt = self._cfg.ping_alt_target
            logger.info("Primary ping failed, trying alt target %s", alt)
            rtts, sent = await collect_ping(alt, count=count)
            if rtts:
                target = alt

        stats = compute_ping_stats(rtts, sent)
        self._db.record_fast_metric(
            stats.rtt_avg, stats.rtt_min, stats.rtt_max,
            stats.jitter, stats.packet_loss,
        )

        self._state["fast_metrics"] = {
            "rtt_avg":     stats.rtt_avg,
            "rtt_min":     stats.rtt_min,
            "rtt_max":     stats.rtt_max,
            "rtt_mdev":    stats.rtt_mdev,
            "jitter":      stats.jitter,
            "packet_loss": stats.packet_loss,
            "samples":     stats.samples,
            "target":      target,
        }
        self._state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._flush()

        logger.debug(
            "fast ← RTT=%.1fms  jitter=%.1fms  loss=%.1f%%",
            stats.rtt_avg, stats.jitter, stats.packet_loss,
        )

    async def _collect_slow(self) -> None:
        result = await collect_throughput(
            method=self._cfg.throughput_method,
            iperf3_server=self._cfg.iperf3_server,
            timeout=self._cfg.throughput_timeout,
        )
        if result is None:
            return

        self._db.record_slow_metric(
            result.download_mbps, result.upload_mbps, result.method
        )
        self._state["slow_metrics"] = {
            "download_mbps": result.download_mbps,
            "upload_mbps":   result.upload_mbps,
            "method":        result.method,
            "last_tested":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._flush()

    async def _collect_ip(self) -> None:
        info = await collect_ip_info()
        if info is None:
            return

        self._ip_tracker.check_and_record(
            current_ip=info["ip"],
            isp=info.get("isp", ""),
            asn=info.get("asn", ""),
            country=info.get("country", ""),
            city=info.get("city", ""),
        )

        ip_type, reason = self._ip_tracker.get_ip_type()
        last_change     = self._ip_tracker.get_last_change_time()

        self._state["ip_info"]        = info
        self._state["ip_type"]        = ip_type
        self._state["ip_type_reason"] = reason
        self._state["last_ip_change"] = (
            last_change.strftime("%Y-%m-%d %H:%M UTC")
            if last_change
            else "No change recorded"
        )
        self._flush()

        logger.debug("ip ← %s  type=%s", info["ip"], ip_type)

    # ---------------------------------------------------------------- #
    # Lifecycle                                                          #
    # ---------------------------------------------------------------- #

    async def run(self) -> None:
        logger.info("NStatus daemon starting…")
        self._cfg.data_dir.mkdir(parents=True, exist_ok=True)

        # Write a "starting" placeholder so Conky shows something immediately
        self._flush()

        tasks = [
            asyncio.create_task(self._fast_loop(),    name="fast_loop"),
            asyncio.create_task(self._slow_loop(),    name="slow_loop"),
            asyncio.create_task(self._ip_loop(),      name="ip_loop"),
            asyncio.create_task(self._cleanup_loop(), name="cleanup_loop"),
        ]

        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except asyncio.CancelledError:
            logger.info("Daemon received cancellation — shutting down")
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("NStatus daemon stopped.")

    def request_stop(self) -> None:
        logger.info("Stop requested")
        self._running = False


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

def _resolve_config() -> Config:
    path = os.environ.get("NSTATUS_CONFIG")
    if not path:
        candidate = Path.home() / ".config" / "nstatus" / "config.yaml"
        if candidate.exists():
            path = str(candidate)
    return Config(path)


def main() -> None:
    config = _resolve_config()
    _setup_logging(config)

    daemon = NStatusDaemon(config)
    loop   = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_signal(signame: str) -> None:
        logger.info("Received %s", signame)
        daemon.request_stop()
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig.name: _handle_signal(s))

    try:
        loop.run_until_complete(daemon.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
NStatus — Desktop Network Monitor daemon for Ubuntu.

Async loops
───────────
  fast_loop       — ping QoS + DNS latency + gateway latency  (fast_interval s)
  slow_loop       — throughput test                            (slow_interval s)
  ip_loop         — public IP + ISP + IPv6 check              (ip_check_interval s)
  wan_loop        — WAN type detection via tracepath PMTU      (30 min)
  cloudflare_loop — probe Cloudflare-served endpoints         (cf_check_interval s)
  history_loop    — pull 1 h / 24 h averages from SQLite      (5 min)
  cleanup_loop    — prune old DB records                       (24 h)

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
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config
from src.storage.database import Database
from src.storage.state_writer import write_conky_data, write_state
from src.collector.ping_collector import collect_ping
from src.collector.ip_collector import collect_ip_info
from src.collector.throughput_collector import collect_throughput
from src.collector.dns_collector import collect_dns_latency
from src.collector.gateway_collector import collect_gateway_info, collect_ipv6_status
from src.collector.cloudflare_collector import probe_all_endpoints
from src.collector.wan_type_collector import collect_wan_type
from src.analyzer.stats import compute_ping_stats
from src.analyzer.ip_tracker import IPTracker
from src.analyzer.quality_score import compute_quality_score, score_label, score_color


# ------------------------------------------------------------------ #
# Logging setup                                                        #
# ------------------------------------------------------------------ #

def _setup_logging(config: Config) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    level_name = config.get("logging", "level", default="INFO")
    level      = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO
        print(f"[nstatus] Invalid logging level '{level_name}', defaulting to INFO")

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid adding duplicate handlers if called more than once.
    if root.handlers:
        return

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    rh = logging.handlers.RotatingFileHandler(
        config.log_dir / "nstatus.log",
        maxBytes=config.get("logging", "max_bytes",    default=10_485_760),
        backupCount=config.get("logging", "backup_count", default=3),
    )
    rh.setFormatter(fmt)
    root.addHandler(rh)

    logging.getLogger("asyncio").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Daemon                                                               #
# ------------------------------------------------------------------ #

_INITIAL_STATE: Dict[str, Any] = {
    "updated_at":           "Starting…",
    "fast_metrics":         {},
    "slow_metrics":         {},
    "dns_metrics":          {},
    "gateway_metrics":      {},
    "ipv6":                 {},
    "ip_info":              {},
    "ip_type":              "UNCERTAIN",
    "ip_type_reason":       "Not yet checked",
    "last_ip_change":       "Unknown",
    "history_1h":           {},
    "history_24h":          {},
    "quality_score":        None,
    "quality_label":        "N/A",
    "quality_color":        "#888888",
    "cloudflare_endpoints": [],   # list of probe result dicts
    "wan_info":             {},   # wan_type / wan_mtu / method
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

    def _update_quality_score(self) -> None:
        fm  = self._state.get("fast_metrics", {})
        dns = self._state.get("dns_metrics", {})
        score = compute_quality_score(
            rtt_avg=fm.get("rtt_avg"),
            jitter=fm.get("jitter"),
            packet_loss=fm.get("packet_loss"),
            dns_ms=dns.get("dns_ms"),
        )
        self._state["quality_score"] = score
        self._state["quality_label"] = score_label(score)
        self._state["quality_color"] = score_color(score)

    # ---------------------------------------------------------------- #
    # Loops                                                              #
    # ---------------------------------------------------------------- #

    async def _fast_loop(self) -> None:
        interval = self._cfg.fast_interval
        logger.info("fast_loop started  (interval=%ds)", interval)
        while self._running:
            try:
                await self._collect_fast()
            except Exception as exc:
                logger.error("fast_loop error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def _slow_loop(self) -> None:
        interval = self._cfg.slow_interval
        logger.info("slow_loop started  (interval=%ds)", interval)
        await asyncio.sleep(45)   # stagger to avoid startup overload
        while self._running:
            try:
                await self._collect_slow()
            except Exception as exc:
                logger.error("slow_loop error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def _ip_loop(self) -> None:
        interval = self._cfg.ip_check_interval
        logger.info("ip_loop started    (interval=%ds)", interval)
        while self._running:
            try:
                await self._collect_ip()
            except Exception as exc:
                logger.error("ip_loop error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def _wan_loop(self) -> None:
        """Detect WAN type via tracepath PMTU — run once then every 30 min."""
        logger.info("wan_loop started   (interval=1800s)")
        while self._running:
            try:
                await self._collect_wan()
            except Exception as exc:
                logger.error("wan_loop error: %s", exc, exc_info=True)
            await asyncio.sleep(1800)

    async def _cloudflare_loop(self) -> None:
        """Probe all Cloudflare endpoints at cf_check_interval seconds."""
        endpoints = self._cfg.cloudflare_endpoints
        if not endpoints:
            logger.info("cloudflare_loop: no endpoints configured, skipping")
            return
        interval = self._cfg.cloudflare_check_interval
        timeout  = self._cfg.cloudflare_timeout
        logger.info(
            "cloudflare_loop started (%d endpoint(s), interval=%ds)",
            len(endpoints), interval,
        )
        while self._running:
            try:
                await self._collect_cloudflare()
            except Exception as exc:
                logger.error("cloudflare_loop error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def _history_loop(self) -> None:
        """Refresh 1 h and 24 h historical averages from SQLite every 10 min."""
        logger.info("history_loop started (interval=600s)")
        while self._running:
            try:
                self._state["history_1h"]  = self._db.get_fast_averages(hours=1)
                self._state["history_24h"] = self._db.get_fast_averages(hours=24)
                self._flush()
            except Exception as exc:
                logger.error("history_loop error: %s", exc)
            await asyncio.sleep(600)

    async def _cleanup_loop(self) -> None:
        cfg = self._cfg
        interval_s = cfg.retention_cleanup_interval_hours * 3600
        vacuum_every_s = cfg.retention_vacuum_interval_days * 86_400
        logger.info(
            "cleanup_loop started (interval=%.1fh, vacuum_every=%dd)",
            cfg.retention_cleanup_interval_hours,
            cfg.retention_vacuum_interval_days,
        )
        _last_vacuum = 0.0

        while self._running:
            try:
                self._db.cleanup_old_records(
                    fast_hours=cfg.retention_fast_hours,
                    dns_hours=cfg.retention_dns_hours,
                    cloudflare_days=cfg.retention_cloudflare_days,
                    slow_days=cfg.retention_slow_days,
                    ip_history_days=cfg.retention_ip_history_days,
                    max_fast_rows=cfg.retention_max_fast_rows,
                    max_dns_rows=cfg.retention_max_dns_rows,
                    max_cloudflare_rows=cfg.retention_max_cloudflare_rows,
                    max_slow_rows=cfg.retention_max_slow_rows,
                )
            except Exception as exc:
                logger.error("cleanup_loop error: %s", exc)

            # VACUUM is run in a separate try block so a vacuum failure
            # does not prevent the next cleanup from running.
            now = time.monotonic()
            if now - _last_vacuum >= vacuum_every_s:
                try:
                    self._db.vacuum()
                    _last_vacuum = now
                except Exception as exc:
                    logger.error("DB vacuum error: %s", exc)

            await asyncio.sleep(interval_s)

    # ---------------------------------------------------------------- #
    # Collection tasks                                                   #
    # ---------------------------------------------------------------- #

    async def _collect_fast(self) -> None:
        target = self._cfg.ping_target
        count  = self._cfg.ping_count

        rtts, sent = await collect_ping(target, count=count)

        # Fallback: if primary returns no data, try the alternate target.
        if not rtts:
            alt = self._cfg.ping_alt_target
            if alt != target:
                logger.info("Primary ping returned no data, trying alt %s", alt)
                rtts, sent = await collect_ping(alt, count=count)
                if rtts:
                    target = alt

        stats = compute_ping_stats(rtts, sent)
        self._db.record_fast_metric(
            stats.rtt_avg, stats.rtt_min, stats.rtt_max,
            stats.rtt_mdev, stats.jitter, stats.packet_loss,
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

        # DNS latency (runs concurrently with gateway ping)
        dns_target = self._cfg.get("network", "dns_target", default="google.com")

        dns_result, gw_info = await asyncio.gather(
            collect_dns_latency(hostname=dns_target),
            collect_gateway_info(),
            return_exceptions=False,
        )

        if isinstance(dns_result, tuple):
            dns_ms, dns_srv = dns_result
            if isinstance(dns_ms, float):
                self._db.record_dns_metric(dns_ms, target=dns_target)
                self._state["dns_metrics"] = {"dns_ms": dns_ms, "target": dns_target, "server": dns_srv}
        elif isinstance(dns_result, Exception):
            logger.error("DNS collection raised: %s", dns_result)

        if isinstance(gw_info, dict):
            self._state["gateway_metrics"] = gw_info
        elif isinstance(gw_info, Exception):
            logger.error("Gateway collection raised: %s", gw_info)

        self._update_quality_score()
        self._flush()

        logger.debug(
            "fast ← RTT=%.1fms  jitter=%.1fms  loss=%.1f%%  dns=%.0fms",
            stats.rtt_avg, stats.jitter, stats.packet_loss,
            dns_ms if isinstance(dns_ms, float) else -1,
        )

    async def _collect_slow(self) -> None:
        result = await collect_throughput(
            method=self._cfg.throughput_method,
            iperf3_server=self._cfg.iperf3_server,
            timeout=self._cfg.throughput_timeout,
        )
        if result is None:
            return
        self._db.record_slow_metric(result.download_mbps, result.upload_mbps, result.method)
        self._state["slow_metrics"] = {
            "download_mbps": result.download_mbps,
            "upload_mbps":   result.upload_mbps,
            "method":        result.method,
            "last_tested":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._flush()

    async def _collect_ip(self) -> None:
        # Run IP lookup and IPv6 check concurrently.
        ip_info, ipv6 = await asyncio.gather(
            collect_ip_info(),
            collect_ipv6_status(),
            return_exceptions=False,
        )

        if isinstance(ipv6, dict):
            self._state["ipv6"] = ipv6
        elif isinstance(ipv6, Exception):
            logger.error("IPv6 check raised: %s", ipv6)

        if not isinstance(ip_info, dict) or not ip_info:
            self._state["ip_info"] = {}
            self._flush()
            return

        self._ip_tracker.check_and_record(
            current_ip=ip_info["ip"],
            isp=ip_info.get("isp", ""),
            asn=ip_info.get("asn", ""),
            country=ip_info.get("country", ""),
            city=ip_info.get("city", ""),
        )

        ip_type, reason = self._ip_tracker.get_ip_type()
        last_change     = self._ip_tracker.get_last_change_time()

        self._state["ip_info"]        = ip_info
        self._state["ip_type"]        = ip_type
        self._state["ip_type_reason"] = reason
        self._state["last_ip_change"] = (
            last_change.strftime("%Y-%m-%d %H:%M UTC")
            if last_change else "No change recorded"
        )
        self._flush()
        logger.debug("ip ← %s  type=%s", ip_info["ip"], ip_type)

    async def _collect_wan(self) -> None:
        result = await collect_wan_type()
        self._state["wan_info"] = result
        self._flush()
        logger.debug("wan ← type=%s  mtu=%s  method=%s",
                     result.get("wan_type"), result.get("wan_mtu"), result.get("method"))

    async def _collect_cloudflare(self) -> None:
        endpoints = self._cfg.cloudflare_endpoints
        timeout   = self._cfg.cloudflare_timeout
        if not endpoints:
            return

        results = await probe_all_endpoints(endpoints, timeout=timeout)

        serialised: List[Dict] = []
        for r in results:
            # Persist to DB
            self._db.record_cloudflare_probe(
                name=r.name, url=r.url,
                http_status=r.http_status,
                is_up=r.is_up,
                is_cloudflare=r.is_cloudflare,
                cf_ray=r.cf_ray,
                pop_code=r.pop_code,
                cache_status=r.cache_status,
                dns_ms=r.dns_ms or None,
                connect_ms=r.connect_ms or None,
                tls_ms=r.tls_ms or None,
                ttfb_ms=r.ttfb_ms or None,
                total_ms=r.total_ms or None,
            )
            # Pull uptime stats (last 24 h) from DB
            uptime = self._db.get_cloudflare_uptime(r.name, hours=24)

            serialised.append({
                "name":          r.name,
                "url":           r.url,
                "http_status":   r.http_status,
                "is_cloudflare": r.is_cloudflare,
                "is_up":         r.is_up,
                "cf_ray":        r.cf_ray,
                "pop_code":      r.pop_code,
                "pop_city":      r.pop_city,
                "cache_status":  r.cache_status,
                "dns_ms":        r.dns_ms,
                "connect_ms":    r.connect_ms,
                "tls_ms":        r.tls_ms,
                "ttfb_ms":       r.ttfb_ms,
                "total_ms":      r.total_ms,
                "error_msg":     r.error_msg,
                "cf_error_msg":  r.cf_error_msg,
                "uptime_24h":    uptime.get("uptime_pct"),
                "avg_ttfb_24h":  uptime.get("avg_ttfb_ms"),
                "checked_at":    datetime.now().strftime("%H:%M:%S"),
            })

        self._state["cloudflare_endpoints"] = serialised
        self._flush()
        logger.info(
            "CF probes: %s",
            "  ".join(f"{r['name']}={r['http_status']}" for r in serialised),
        )

    # ---------------------------------------------------------------- #
    # Lifecycle                                                          #
    # ---------------------------------------------------------------- #

    async def run(self) -> None:
        logger.info("NStatus daemon starting…")
        self._cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self._flush()   # write "Starting…" placeholder immediately

        tasks = [
            asyncio.create_task(self._fast_loop(),       name="fast_loop"),
            asyncio.create_task(self._slow_loop(),       name="slow_loop"),
            asyncio.create_task(self._ip_loop(),         name="ip_loop"),
            asyncio.create_task(self._wan_loop(),        name="wan_loop"),
            asyncio.create_task(self._cloudflare_loop(), name="cloudflare_loop"),
            asyncio.create_task(self._history_loop(),    name="history_loop"),
            asyncio.create_task(self._cleanup_loop(),    name="cleanup_loop"),
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

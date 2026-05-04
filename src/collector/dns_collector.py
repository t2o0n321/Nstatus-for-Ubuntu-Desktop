"""DNS latency collector — measures query time using the `dig` utility."""

import asyncio
import logging
import re
import subprocess
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Matches: ";; Query time: 23 msec"
_QUERY_TIME_RE = re.compile(r"Query time:\s*(\d+)\s*msec")


def _dns_candidates() -> List[str]:
    """
    Return an ordered list of DNS servers to try, best-for-local-analysis first.

    Order:
      1. Default gateway  — LAN round-trip, best for local health analysis
      2. Non-loopback nameservers from /etc/resolv.conf  — ISP DNS (WAN-close)
      3. 8.8.8.8          — guaranteed fallback
    """
    candidates: List[str] = []

    # Default gateway
    try:
        out = subprocess.check_output(
            ["ip", "route", "show", "default"], text=True, timeout=2
        )
        for line in out.splitlines():
            m = re.search(r"via\s+(\S+)", line)
            if m:
                candidates.append(m.group(1))
                break
    except Exception:
        pass

    # Non-loopback nameservers from resolv.conf
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    ns = line.split()[1]
                    if not ns.startswith("127.") and ns != "::1" and ns not in candidates:
                        candidates.append(ns)
    except Exception:
        pass

    if "8.8.8.8" not in candidates:
        candidates.append("8.8.8.8")

    return candidates


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.communicate()
    except Exception:
        pass


async def _query_one(hostname: str, dns_server: str, timeout: int) -> Optional[float]:
    """Single dig query — returns ms or None."""
    cmd = [
        "dig", f"@{dns_server}", hostname,
        "+noall", "+stats", "+time=3", "+tries=1",
    ]
    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            await _kill(proc)
        return None
    except FileNotFoundError:
        logger.error("'dig' not found — install: sudo apt install dnsutils")
        return None
    except Exception as exc:
        logger.error("DNS query failed: %s", exc)
        if proc is not None:
            await _kill(proc)
        return None

    m = _QUERY_TIME_RE.search(stdout.decode(errors="replace"))
    if not m:
        return None
    return float(m.group(1))


async def collect_dns_latency(
    hostname: str = "google.com",
    dns_server: str = "",
    timeout: int = 6,
) -> Tuple[Optional[float], str]:
    """
    Measure DNS latency, trying the best available server first.

    If *dns_server* is given, only that server is tried.
    Otherwise tries: gateway → resolv.conf nameservers → 8.8.8.8
    in order until one responds.

    Returns (latency_ms, server_used).  latency_ms is None on total failure.
    """
    if dns_server:
        servers = [dns_server]
    else:
        servers = _dns_candidates()

    for srv in servers:
        ms = await _query_one(hostname, srv, timeout)
        if ms is not None:
            logger.debug("DNS latency: %.0f ms  (server=%s host=%s)", ms, srv, hostname)
            return ms, srv
        logger.debug("DNS server %s did not respond, trying next", srv)

    logger.warning("All DNS servers failed for host=%s", hostname)
    return None, servers[0] if servers else "8.8.8.8"

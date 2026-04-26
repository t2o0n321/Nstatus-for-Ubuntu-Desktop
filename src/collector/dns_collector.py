"""DNS latency collector — measures query time using the `dig` utility."""

import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Matches: ";; Query time: 23 msec"
_QUERY_TIME_RE = re.compile(r"Query time:\s*(\d+)\s*msec")


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.communicate()
    except Exception:
        pass


async def collect_dns_latency(
    hostname: str = "google.com",
    dns_server: str = "8.8.8.8",
    timeout: int = 10,
) -> Optional[float]:
    """
    Measure DNS resolution latency for *hostname* against *dns_server*.

    Uses `dig` with +time=N and +tries=1 to avoid retries skewing results.
    Returns latency in milliseconds, or None on failure.

    Why DNS latency matters:
      High DNS latency (>100 ms) makes every new connection feel slow even
      when raw bandwidth is fine.  This measurement isolates DNS from general
      network latency.
    """
    cmd = [
        "dig",
        f"@{dns_server}",
        hostname,
        "+stats",
        "+noall",
        "+time=5",
        "+tries=1",
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
        logger.warning("dig timed out after %ds (server=%s)", timeout, dns_server)
        if proc is not None:
            await _kill(proc)
        return None
    except FileNotFoundError:
        logger.error("'dig' not found — install: sudo apt install dnsutils")
        return None
    except Exception as exc:
        logger.error("DNS latency collection failed: %s", exc)
        if proc is not None:
            await _kill(proc)
        return None

    output = stdout.decode(errors="replace")
    m = _QUERY_TIME_RE.search(output)
    if not m:
        logger.debug("No query time in dig output (server=%s host=%s)", dns_server, hostname)
        return None

    ms = float(m.group(1))
    logger.debug("DNS latency: %.0f ms  (server=%s host=%s)", ms, dns_server, hostname)
    return ms

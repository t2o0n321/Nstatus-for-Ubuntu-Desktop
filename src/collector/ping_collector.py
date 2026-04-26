"""Async ping collector — measures RTT, jitter, and packet loss via ICMP."""

import asyncio
import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# time=12.3 ms  or  time=12 ms
_RTT_RE = re.compile(r"time=(\d+(?:\.\d+)?)\s*ms")

# "10 packets transmitted, 9 received"  (with or without the word "packets")
_SUMMARY_RE = re.compile(
    r"(\d+)\s+packets\s+transmitted,\s+(\d+)\s+(?:packets\s+)?received"
)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Best-effort subprocess termination after a timeout."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.communicate()
    except Exception:
        pass


async def collect_ping(
    target: str,
    count: int = 10,
    timeout: int = 30,
    interval: float = 0.2,
) -> Tuple[List[float], int]:
    """
    Send *count* ICMP echo requests to *target* and parse the results.

    Returns:
        (rtts, packets_sent)
        ``rtts`` contains round-trip times in ms for received packets only.

    Never raises — returns ([], count) on any failure so callers can
    compute 100 % packet loss.
    """
    cmd = [
        "ping",
        "-c", str(count),
        "-W", "2",            # 2-second per-packet receive timeout
        "-i", str(interval),  # 0.2 s inter-packet interval (minimum for non-root)
        target,
    ]

    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning("ping to %s timed out after %ds", target, timeout)
        if proc is not None:
            await _kill(proc)
        return [], count
    except FileNotFoundError:
        logger.error("'ping' binary not found on PATH")
        return [], count
    except Exception as exc:
        logger.error("ping to %s failed: %s", target, exc)
        if proc is not None:
            await _kill(proc)
        return [], count

    output = stdout.decode(errors="replace")
    rtts   = [float(m) for m in _RTT_RE.findall(output)]

    sent = count
    m = _SUMMARY_RE.search(output)
    if m:
        sent = int(m.group(1))

    if proc.returncode not in (0, 1):
        logger.warning(
            "ping exited %d: %s",
            proc.returncode,
            stderr.decode(errors="replace")[:120],
        )

    logger.debug("ping %s → %d/%d replies, samples=%s", target, len(rtts), sent, rtts[:3])
    return rtts, sent

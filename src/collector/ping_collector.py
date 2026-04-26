"""Async ping collector — measures RTT, jitter, and packet loss via ICMP."""

import asyncio
import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Matches lines like:  64 bytes from 8.8.8.8: icmp_seq=1 ttl=118 time=12.3 ms
_RTT_RE = re.compile(r"time=(\d+\.?\d*)\s*ms")

# Matches the summary line:  10 packets transmitted, 9 received, 10% packet loss
_SUMMARY_RE = re.compile(
    r"(\d+)\s+packets\s+transmitted,\s+(\d+)\s+(?:packets\s+)?received"
)


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
        where ``rtts`` is a list of round-trip times in milliseconds
        (only for packets that received a reply).

    Never raises — returns ([], count) on any failure so the caller
    can still compute 100 % packet loss.
    """
    cmd = [
        "ping",
        "-c", str(count),
        "-W", "2",           # 2-second per-packet wait
        "-i", str(interval), # inter-packet interval
        target,
    ]

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
        return [], count
    except FileNotFoundError:
        logger.error("'ping' binary not found on PATH")
        return [], count
    except Exception as exc:
        logger.error("ping to %s failed: %s", target, exc)
        return [], count

    output = stdout.decode(errors="replace")
    rtts   = [float(m) for m in _RTT_RE.findall(output)]

    sent = count
    m = _SUMMARY_RE.search(output)
    if m:
        sent = int(m.group(1))

    if proc.returncode not in (0, 1):
        # returncode 1 = some packets lost (normal); >1 = error
        logger.warning(
            "ping exited with code %d: %s",
            proc.returncode,
            stderr.decode(errors="replace")[:120],
        )

    logger.debug(
        "ping %s → %d/%d replies, first_rtts=%s",
        target, len(rtts), sent, rtts[:3],
    )
    return rtts, sent

"""Throughput collector — supports speedtest-cli and iperf3 backends."""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ThroughputResult:
    download_mbps: float
    upload_mbps: float
    method: str


# ------------------------------------------------------------------ #
# speedtest-cli backend                                                #
# ------------------------------------------------------------------ #

async def _run_speedtest(timeout: int) -> Optional[ThroughputResult]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "speedtest-cli", "--json", "--secure",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning("speedtest-cli timed out after %ds", timeout)
        return None
    except FileNotFoundError:
        logger.error(
            "speedtest-cli not found — install with: pip install speedtest-cli"
        )
        return None
    except Exception as exc:
        logger.error("speedtest-cli subprocess error: %s", exc)
        return None

    if proc.returncode != 0:
        logger.warning(
            "speedtest-cli exited %d: %s",
            proc.returncode,
            stderr.decode(errors="replace")[:200],
        )
        return None

    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        logger.error("speedtest-cli JSON parse failed: %s", exc)
        return None

    return ThroughputResult(
        download_mbps=round(data["download"] / 1_000_000, 2),
        upload_mbps=round(data["upload"] / 1_000_000, 2),
        method="speedtest-cli",
    )


# ------------------------------------------------------------------ #
# iperf3 backend                                                       #
# ------------------------------------------------------------------ #

async def _iperf3_direction(
    server: str, reverse: bool, timeout: int
) -> Optional[float]:
    """Run one iperf3 test, return bits-per-second or None."""
    cmd = ["iperf3", "-c", server, "-J", "-t", "10"]
    if reverse:
        cmd.append("-R")  # server→client = download
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        data = json.loads(stdout.decode())
        return data["end"]["sum_received"]["bits_per_second"]
    except asyncio.TimeoutError:
        logger.warning("iperf3 test to %s timed out", server)
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("iperf3 result parse failed: %s", exc)
    except FileNotFoundError:
        logger.error("iperf3 not found — install with: sudo apt install iperf3")
    except Exception as exc:
        logger.error("iperf3 error: %s", exc)
    return None


async def _run_iperf3(server: str, timeout: int) -> Optional[ThroughputResult]:
    if not server:
        logger.error("iperf3_server is not configured in config.yaml")
        return None

    # Run download then upload sequentially (iperf3 server handles one at a time)
    dl_bps = await _iperf3_direction(server, reverse=True,  timeout=timeout)
    ul_bps = await _iperf3_direction(server, reverse=False, timeout=timeout)

    if dl_bps is None and ul_bps is None:
        return None

    return ThroughputResult(
        download_mbps=round((dl_bps or 0) / 1_000_000, 2),
        upload_mbps=round((ul_bps or 0) / 1_000_000, 2),
        method="iperf3",
    )


# ------------------------------------------------------------------ #
# Unified entry point                                                  #
# ------------------------------------------------------------------ #

async def collect_throughput(
    method: str = "speedtest",
    iperf3_server: str = "",
    timeout: int = 120,
) -> Optional[ThroughputResult]:
    """
    Run the configured throughput test and return a ThroughputResult.
    Returns None on any failure so the caller can keep the last known values.
    """
    logger.info("Starting throughput test (method=%s)", method)
    if method == "iperf3":
        result = await _run_iperf3(iperf3_server, timeout)
    else:
        result = await _run_speedtest(timeout)

    if result:
        logger.info(
            "Throughput: DL=%.1f Mbps  UL=%.1f Mbps  [%s]",
            result.download_mbps, result.upload_mbps, result.method,
        )
    else:
        logger.warning("Throughput test returned no result")

    return result

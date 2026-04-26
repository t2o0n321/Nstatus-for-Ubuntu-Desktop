"""
Gateway and connectivity collectors.

  collect_gateway_info()  — detects default gateway IP and LAN latency
  collect_ipv6_status()   — checks whether IPv6 reaches the public internet
"""

import asyncio
import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_RTT_RE       = re.compile(r"time=(\d+(?:\.\d+)?)\s*ms")
_GATEWAY_RE   = re.compile(r"default\s+(?:via\s+)?(\d+\.\d+\.\d+\.\d+)")
_INTERFACE_RE = re.compile(r"dev\s+(\S+)")


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.communicate()
    except Exception:
        pass


# ------------------------------------------------------------------ #
# Default gateway detection                                            #
# ------------------------------------------------------------------ #

async def _get_default_gateway() -> Optional[Dict[str, str]]:
    """
    Parse `ip route show default` to get the gateway IP and interface name.
    Returns a dict with keys 'ip' and 'iface', or None.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ip", "route", "show", "default",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except Exception as exc:
        logger.debug("ip route failed: %s", exc)
        return None

    output = stdout.decode(errors="replace")
    gw_match = _GATEWAY_RE.search(output)
    if_match = _INTERFACE_RE.search(output)
    if not gw_match:
        return None

    return {
        "ip":    gw_match.group(1),
        "iface": if_match.group(1) if if_match else "",
    }


# ------------------------------------------------------------------ #
# LAN / gateway latency                                                #
# ------------------------------------------------------------------ #

async def collect_gateway_info(timeout: int = 15) -> Optional[Dict]:
    """
    Detect the default gateway and measure LAN latency to it with 5 pings.

    Why this matters:
      If gateway RTT is 1 ms but WAN RTT is 80 ms, the bottleneck is the ISP.
      If gateway RTT is also 50 ms, the LAN itself (router, cable) is the issue.

    Returns:
        {
          "gateway_ip":   "192.168.1.1",
          "interface":    "eth0",
          "rtt_avg_ms":   1.2,
          "rtt_min_ms":   0.9,
          "rtt_max_ms":   1.7,
          "packet_loss":  0.0,
        }
    or None if the gateway cannot be determined.
    """
    gw = await _get_default_gateway()
    if not gw:
        logger.debug("Could not determine default gateway")
        return None

    gw_ip = gw["ip"]

    # 5 pings with 0.2 s interval is fast enough (total ≈ 1 s)
    cmd = ["ping", "-c", "5", "-W", "2", "-i", "0.2", gw_ip]
    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Gateway ping timed out")
        if proc is not None:
            await _kill(proc)
        return {"gateway_ip": gw_ip, "interface": gw["iface"], "error": "timeout"}
    except Exception as exc:
        logger.error("Gateway ping failed: %s", exc)
        return None

    output = stdout.decode(errors="replace")
    rtts   = [float(m) for m in _RTT_RE.findall(output)]

    loss_m = re.search(r"(\d+(?:\.\d+)?)%\s+packet\s+loss", output)
    loss   = float(loss_m.group(1)) if loss_m else (0.0 if rtts else 100.0)

    result: Dict = {
        "gateway_ip": gw_ip,
        "interface":  gw["iface"],
        "packet_loss": round(loss, 1),
    }
    if rtts:
        result["rtt_avg_ms"] = round(sum(rtts) / len(rtts), 2)
        result["rtt_min_ms"] = round(min(rtts), 2)
        result["rtt_max_ms"] = round(max(rtts), 2)
    else:
        result["rtt_avg_ms"] = None
        result["rtt_min_ms"] = None
        result["rtt_max_ms"] = None

    logger.debug(
        "Gateway %s: RTT=%.1f ms  loss=%.0f%%",
        gw_ip,
        result.get("rtt_avg_ms") or 0,
        loss,
    )
    return result


# ------------------------------------------------------------------ #
# IPv6 connectivity check                                              #
# ------------------------------------------------------------------ #

_IPV6_TARGETS = [
    "2001:4860:4860::8888",   # Google DNS
    "2606:4700:4700::1111",   # Cloudflare DNS
]


async def collect_ipv6_status(timeout: int = 8) -> Dict[str, object]:
    """
    Check whether IPv6 can reach the public internet.

    Sends 3 ICMPv6 pings to a known IPv6 address.

    Returns:
        {"available": True, "rtt_ms": 12.3}    on success
        {"available": False, "rtt_ms": None}   on failure
    """
    for target in _IPV6_TARGETS:
        cmd = ["ping6", "-c", "3", "-W", "2", "-i", "0.5", target]
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
            continue
        except FileNotFoundError:
            # ping6 not available; try ping -6
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ping", "-6", "-c", "3", "-W", "2", target,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except Exception:
                continue
        except Exception:
            continue

        output = stdout.decode(errors="replace")
        rtts   = [float(m) for m in _RTT_RE.findall(output)]
        if rtts:
            rtt = round(sum(rtts) / len(rtts), 1)
            logger.debug("IPv6 reachable via %s, RTT=%.1f ms", target, rtt)
            return {"available": True, "rtt_ms": rtt}

    logger.debug("IPv6 not reachable")
    return {"available": False, "rtt_ms": None}

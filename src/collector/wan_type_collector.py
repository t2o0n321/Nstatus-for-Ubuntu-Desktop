"""
WAN connection-type detector.

Strategy
────────
1. Check for a PPP interface on this host  (direct PPPoE dial-up case)
2. Run  tracepath -n -m 8 <target>  and inspect Path-MTU changes:
     pmtu 1492  → PPPoE   (8-byte PPPoE/PPP header overhead)
     pmtu 1500  → IPoE    (standard Ethernet, no overhead)
     other      → TUNNELED (VPN or other encapsulation)
3. Return UNKNOWN on timeout or missing tool.
"""

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

_TRACEPATH_TARGET  = "8.8.8.8"
_TRACEPATH_MAXHOPS = "8"
_TRACEPATH_TIMEOUT = 20   # seconds


async def collect_wan_type() -> dict:
    # ── Method 1: PPP interface on this host ──────────────────────── #
    try:
        proc = await asyncio.create_subprocess_exec(
            "ip", "link", "show", "type", "ppp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if stdout.strip():
            logger.debug("wan_type: PPPoE (ppp interface present)")
            return {"wan_type": "PPPoE", "wan_mtu": None, "method": "ppp_iface"}
    except Exception as exc:
        logger.debug("wan_type ppp check failed: %s", exc)

    # ── Method 2: tracepath PMTU detection ────────────────────────── #
    try:
        proc = await asyncio.create_subprocess_exec(
            "tracepath", "-n", "-m", _TRACEPATH_MAXHOPS, _TRACEPATH_TARGET,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TRACEPATH_TIMEOUT)
        output = stdout.decode(errors="replace")

        pmtu_values = [int(m) for m in re.findall(r"pmtu\s+(\d+)", output)]
        if not pmtu_values:
            logger.debug("wan_type: no pmtu in tracepath output")
            return {"wan_type": "UNKNOWN", "wan_mtu": None, "method": "tracepath"}

        min_pmtu = min(pmtu_values)
        if min_pmtu <= 1492:
            wan_type = "PPPoE"
        else:
            wan_type = "IPoE"

        logger.debug("wan_type: %s  pmtu=%d", wan_type, min_pmtu)
        return {"wan_type": wan_type, "wan_mtu": min_pmtu, "method": "tracepath"}

    except asyncio.TimeoutError:
        logger.warning("wan_type: tracepath timed out")
        return {"wan_type": "UNKNOWN", "wan_mtu": None, "method": "timeout"}
    except FileNotFoundError:
        logger.warning("wan_type: tracepath not found (install iputils-tracepath)")
        return {"wan_type": "UNKNOWN", "wan_mtu": None, "method": "not_found"}
    except Exception as exc:
        logger.warning("wan_type: tracepath error: %s", exc)
        return {"wan_type": "UNKNOWN", "wan_mtu": None, "method": "error"}

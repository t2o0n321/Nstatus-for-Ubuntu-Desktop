"""Public IP and ISP information collector using ipinfo.io (free, no auth)."""

import asyncio
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_ENDPOINTS = [
    "http://ip-api.com/json",   # primary — dedicated isp field, consumer brand name
    "https://ipinfo.io/json",   # fallback — uses ASN org name instead
]


def _parse_ipinfo(data: dict) -> Dict[str, str]:
    """Parse ipinfo.io response."""
    org = data.get("org", "")        # e.g. "AS15169 Google LLC"
    asn, isp = "", org
    if org.startswith("AS"):
        parts = org.split(" ", 1)
        asn = parts[0]
        isp = parts[1] if len(parts) > 1 else org
    return {
        "ip":       data.get("ip",       ""),
        "isp":      isp,
        "asn":      asn,
        "country":  data.get("country",  ""),
        "city":     data.get("city",     ""),
        "org":      org,
        "hostname": data.get("hostname", ""),
    }


def _parse_ipapi(data: dict) -> Dict[str, str]:
    """Parse ip-api.com response (fallback)."""
    return {
        "ip":       data.get("query",       ""),
        "isp":      data.get("isp",         data.get("org", "")),
        "asn":      data.get("as",          "").split(" ")[0],
        "country":  data.get("countryCode", ""),
        "city":     data.get("city",        ""),
        "org":      data.get("org",         ""),
        "hostname": data.get("reverse",     ""),
    }


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.communicate()
    except Exception:
        pass


async def _fetch(url: str, timeout: int) -> Optional[dict]:
    """Run curl asynchronously and return parsed JSON, or None on any error."""
    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "--silent",
            "--max-time",       str(timeout),
            "--connect-timeout", "5",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + 5
        )
        if proc.returncode != 0:
            logger.debug("curl returned %d for %s", proc.returncode, url)
            return None
        raw = stdout.decode(errors="replace").strip()
        if not raw:
            logger.debug("Empty response from %s", url)
            return None
        return json.loads(raw)
    except asyncio.TimeoutError:
        logger.warning("curl to %s timed out", url)
        if proc is not None:
            await _kill(proc)
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse error from %s: %s", url, exc)
    except Exception as exc:
        logger.debug("fetch failed (%s): %s", url, exc)
    return None


async def collect_ip_info(timeout: int = 15) -> Optional[Dict[str, str]]:
    """
    Return a dict with keys: ip, isp, asn, country, city, org, hostname.
    Tries primary endpoint first; falls back to secondary.
    Returns None only if both endpoints fail.
    """
    data = await _fetch(_ENDPOINTS[0], timeout)
    if data and data.get("status") == "success":
        logger.debug("IP info from ip-api.com: %s", data.get("query"))
        return _parse_ipapi(data)

    logger.info("ip-api.com failed, trying ipinfo.io")

    data = await _fetch(_ENDPOINTS[1], timeout)
    if data and data.get("ip"):
        logger.debug("IP info from ipinfo.io: %s", data.get("ip"))
        return _parse_ipinfo(data)

    logger.warning("All IP-info endpoints failed")
    return None

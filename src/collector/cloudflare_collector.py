"""
Cloudflare service health collector.

Probes HTTP/HTTPS endpoints behind Cloudflare (CDN or Tunnel) and extracts:
  - HTTP status code + Cloudflare-specific error detection (521-530)
  - Full timing breakdown: DNS / TCP / TLS / TTFB / total
  - CF-Ray header  → PoP (data-centre) identification
  - CF-Cache-Status (HIT / MISS / BYPASS / DYNAMIC / EXPIRED / …)
  - Server: cloudflare confirmation

Uses `curl` with -D (dump headers to stdout) and -w (write-out timing).
The response body is discarded so large pages don't waste bandwidth.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Cloudflare constants                                                 #
# ------------------------------------------------------------------ #

# Cloudflare-originated HTTP errors and their meanings.
_CF_ERROR_MESSAGES: Dict[int, str] = {
    520: "Unknown error (origin)",
    521: "Origin web server is down",
    522: "Connection timed out to origin",
    523: "Origin unreachable",
    524: "Cloudflare gateway timeout",
    525: "SSL handshake failed",
    526: "Invalid SSL certificate on origin",
    527: "Railgun listener error",
    530: "DNS resolution failure (check CF dashboard)",
}

# 3-letter IATA PoP codes → city names
_CF_POP_MAP: Dict[str, str] = {
    # Asia-Pacific
    "NRT": "Tokyo",      "KIX": "Osaka",       "TPE": "Taipei",
    "HKG": "Hong Kong",  "SIN": "Singapore",   "SEL": "Seoul",
    "SYD": "Sydney",     "MEL": "Melbourne",   "BNE": "Brisbane",
    "BOM": "Mumbai",     "DEL": "Delhi",        "MAA": "Chennai",
    "MNL": "Manila",     "CGK": "Jakarta",      "KUL": "KL",
    "BKK": "Bangkok",    "SGN": "Ho Chi Minh",  "PNH": "Phnom Penh",
    # Europe
    "LHR": "London",     "AMS": "Amsterdam",    "FRA": "Frankfurt",
    "CDG": "Paris",      "MAD": "Madrid",       "MXP": "Milan",
    "WAW": "Warsaw",     "ARN": "Stockholm",    "OSL": "Oslo",
    "HEL": "Helsinki",   "VIE": "Vienna",       "PRG": "Prague",
    # Americas
    "IAD": "Washington", "JFK": "New York",     "ORD": "Chicago",
    "MIA": "Miami",      "ATL": "Atlanta",      "DFW": "Dallas",
    "LAX": "Los Angeles","SJC": "San Jose",     "SEA": "Seattle",
    "DEN": "Denver",     "YYZ": "Toronto",      "YVR": "Vancouver",
    "GRU": "São Paulo",  "BOG": "Bogotá",       "SCL": "Santiago",
    # Middle East / Africa
    "DXB": "Dubai",      "TLV": "Tel Aviv",     "JNB": "Johannesburg",
}

# curl write-out: key:value pairs separated by | on one line
_CURL_WRITE_OUT = (
    "NSTATUS_CODE:%{http_code}"
    "|NSTATUS_DNS:%{time_namelookup}"
    "|NSTATUS_CONNECT:%{time_connect}"
    "|NSTATUS_TLS:%{time_appconnect}"
    "|NSTATUS_TTFB:%{time_starttransfer}"
    "|NSTATUS_TOTAL:%{time_total}"
)

_CF_RAY_RE    = re.compile(r"^CF-Ray:\s*([A-Za-z0-9]+-([A-Z]{3}))",  re.IGNORECASE | re.MULTILINE)
_CF_CACHE_RE  = re.compile(r"^CF-Cache-Status:\s*(\S+)",              re.IGNORECASE | re.MULTILINE)
_SERVER_RE    = re.compile(r"^Server:\s*(\S+)",                       re.IGNORECASE | re.MULTILINE)
_STATUS_RE    = re.compile(r"^HTTP/[\d.]+ (\d{3})",                   re.MULTILINE)
_CODE_RE      = re.compile(r"NSTATUS_CODE:(\d+)")


# ------------------------------------------------------------------ #
# Result dataclass                                                     #
# ------------------------------------------------------------------ #

@dataclass
class CloudflareProbeResult:
    url:          str
    name:         str
    http_status:  int    # 200, 404, 521, 0 = connection failed
    is_cloudflare:bool   # CF-Ray or Server:cloudflare present
    is_up:        bool   # 2xx or 3xx
    cf_ray:       str = ""   # e.g. "7d3fabc-NRT"
    pop_code:     str = ""   # e.g. "NRT"
    pop_city:     str = ""   # e.g. "Tokyo"
    cache_status: str = ""   # HIT / MISS / BYPASS / DYNAMIC / …
    dns_ms:       float = 0.0
    connect_ms:   float = 0.0
    tls_ms:       float = 0.0   # TLS-only (appconnect − connect)
    ttfb_ms:      float = 0.0
    total_ms:     float = 0.0
    error_msg:    str = ""   # connection/timeout errors
    cf_error_msg: str = ""   # Cloudflare 5xx description


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.communicate()
    except Exception:
        pass


def _parse_timing(line: str) -> Dict[str, float]:
    """Convert the curl write-out line into {key: ms} dict."""
    out: Dict[str, float] = {}
    for part in line.split("|"):
        k, _, v = part.partition(":")
        try:
            out[k.strip()] = round(float(v.strip()) * 1000, 1)
        except ValueError:
            pass
    return out


def _pop_city(code: str) -> str:
    return _CF_POP_MAP.get(code.upper(), code)


# ------------------------------------------------------------------ #
# Single-endpoint probe                                                #
# ------------------------------------------------------------------ #

async def probe_endpoint(
    url: str,
    name: str = "",
    timeout: int = 10,
    follow_redirects: bool = True,
) -> CloudflareProbeResult:
    """
    Send an HTTP(S) probe to *url* and return a CloudflareProbeResult.
    Never raises — returns is_up=False on any error.
    """
    cmd = [
        "curl",
        "--silent",
        "--max-time",        str(timeout),
        "--connect-timeout", "5",
        "--dump-header",     "-",          # headers → stdout
        "--output",          "/dev/null",  # body → /dev/null
        "--write-out",       f"\n{_CURL_WRITE_OUT}",
        "--user-agent",      "NStatus-Monitor/1.0",
    ]
    if follow_redirects:
        cmd += ["--location", "--max-redirs", "3"]
    cmd.append(url)

    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
    except asyncio.TimeoutError:
        logger.warning("CF probe timed out: %s", name or url)
        if proc is not None:
            await _kill(proc)
        return CloudflareProbeResult(url=url, name=name, http_status=0,
                                     is_cloudflare=False, is_up=False,
                                     error_msg="Request timed out")
    except FileNotFoundError:
        logger.error("curl not found — cannot probe Cloudflare endpoints")
        return CloudflareProbeResult(url=url, name=name, http_status=0,
                                     is_cloudflare=False, is_up=False,
                                     error_msg="curl not installed")
    except Exception as exc:
        logger.error("CF probe failed (%s): %s", url, exc)
        if proc is not None:
            await _kill(proc)
        return CloudflareProbeResult(url=url, name=name, http_status=0,
                                     is_cloudflare=False, is_up=False,
                                     error_msg=str(exc)[:80])

    raw = stdout.decode(errors="replace")

    # Split the output: headers come first, timing line after
    header_block = raw
    timing_line  = ""
    marker = "NSTATUS_CODE:"
    if marker in raw:
        idx          = raw.index(marker)
        header_block = raw[:idx]
        timing_line  = raw[idx:].rstrip()

    # HTTP status: parse directly from write-out string as an integer.
    # Do NOT use _parse_timing() for this — that function multiplies all
    # values by 1000 (seconds→ms), which would turn 200 into 200000.
    http_status = 0
    code_m = _CODE_RE.search(timing_line)
    if code_m:
        http_status = int(code_m.group(1))
    if not http_status:
        status_lines = _STATUS_RE.findall(header_block)
        http_status  = int(status_lines[-1]) if status_lines else 0

    timing = _parse_timing(timing_line)

    # Cloudflare detection
    server_m  = _SERVER_RE.search(header_block)
    is_cf     = "cloudflare" in (server_m.group(1).lower() if server_m else "")

    # CF-Ray  → Ray ID + PoP
    ray_m    = _CF_RAY_RE.search(header_block)
    cf_ray   = ray_m.group(1) if ray_m else ""
    pop_code = ray_m.group(2).upper() if ray_m else ""
    if cf_ray:
        is_cf = True   # Ray header is definitive

    # CF-Cache-Status
    cache_m      = _CF_CACHE_RE.search(header_block)
    cache_status = cache_m.group(1).upper() if cache_m else ""

    # TLS time = appconnect − connect  (both are cumulative from start)
    connect_ms = timing.get("NSTATUS_CONNECT", 0.0)
    tls_ms     = round(max(timing.get("NSTATUS_TLS", 0.0) - connect_ms, 0.0), 1)

    is_up        = 200 <= http_status < 400
    cf_error_msg = _CF_ERROR_MESSAGES.get(http_status, "") if not is_up else ""

    result = CloudflareProbeResult(
        url=url, name=name or url,
        http_status=http_status,
        is_cloudflare=is_cf,
        is_up=is_up,
        cf_ray=cf_ray,
        pop_code=pop_code,
        pop_city=_pop_city(pop_code),
        cache_status=cache_status,
        dns_ms=timing.get("NSTATUS_DNS",     0.0),
        connect_ms=connect_ms,
        tls_ms=tls_ms,
        ttfb_ms=timing.get("NSTATUS_TTFB",  0.0),
        total_ms=timing.get("NSTATUS_TOTAL", 0.0),
        cf_error_msg=cf_error_msg,
    )

    logger.debug(
        "CF [%s] %d  ttfb=%.0f ms  total=%.0f ms  cache=%s  PoP=%s",
        result.name, http_status, result.ttfb_ms, result.total_ms,
        cache_status or "—", pop_code or "—",
    )
    return result


# ------------------------------------------------------------------ #
# Multi-endpoint probe (concurrent)                                   #
# ------------------------------------------------------------------ #

async def probe_all_endpoints(
    endpoints: List[Dict],
    timeout: int = 10,
) -> List[CloudflareProbeResult]:
    """
    Probe all configured endpoints concurrently via asyncio.gather.
    Each entry in *endpoints* requires 'url' and optionally 'name'.
    """
    if not endpoints:
        return []

    coros = [
        probe_endpoint(ep["url"], name=ep.get("name", ep["url"]), timeout=timeout)
        for ep in endpoints
    ]
    raw_results = await asyncio.gather(*coros, return_exceptions=True)

    out: List[CloudflareProbeResult] = []
    for i, r in enumerate(raw_results):
        if isinstance(r, Exception):
            ep = endpoints[i]
            logger.error("Unhandled error probing %s: %s", ep.get("url"), r)
            out.append(CloudflareProbeResult(
                url=ep["url"], name=ep.get("name", ep["url"]),
                http_status=0, is_cloudflare=False, is_up=False,
                error_msg=str(r)[:80],
            ))
        else:
            out.append(r)
    return out

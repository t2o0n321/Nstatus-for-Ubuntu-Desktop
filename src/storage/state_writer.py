"""Atomic writers for state.json and the Conky display text file."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Atomic write helper                                                  #
# ------------------------------------------------------------------ #

def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically so Conky never reads a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------------ #
# JSON state file                                                      #
# ------------------------------------------------------------------ #

def write_state(state_file: Path, state: Dict[str, Any]) -> None:
    _atomic_write(state_file, json.dumps(state, indent=2, default=str))
    logger.debug("state.json written")


# ------------------------------------------------------------------ #
# Formatting helpers                                                   #
# ------------------------------------------------------------------ #

def _c(hex_color: str) -> str:
    """Emit a Conky inline color tag."""
    return f"${{color {hex_color}}}"


def _quality_color(value: Any, good: float, warn: float) -> str:
    if not isinstance(value, (int, float)):
        return "#888888"
    if value <= good:
        return "#00e676"
    if value <= warn:
        return "#ffca28"
    return "#ff5252"


def _f(val: Any, dec: int = 1, unit: str = "") -> str:
    """Format a float value or return 'N/A'."""
    if isinstance(val, (int, float)):
        return f"{val:.{dec}f}{unit}"
    return "N/A"


# ------------------------------------------------------------------ #
# Conky display text                                                   #
# ------------------------------------------------------------------ #

W  = "#cccccc"   # regular value
D  = "#555555"   # dim / label
H  = "#7986cb"   # section header (indigo)
DM = "#888888"   # medium dim


def _section(title: str) -> str:
    return f"{_c(H)}── {title} "


def format_conky_text(state: Dict[str, Any]) -> str:  # noqa: C901
    ts = state.get("updated_at", "---")

    # ── QoS ──────────────────────────────────────────────────────── #
    fm   = state.get("fast_metrics", {})
    rtt  = fm.get("rtt_avg")
    rtt_min  = fm.get("rtt_min")
    rtt_max  = fm.get("rtt_max")
    jitter   = fm.get("jitter")
    loss     = fm.get("packet_loss")
    target   = fm.get("target", "?")

    rtt_c    = _quality_color(rtt,    30,  100)
    jitter_c = _quality_color(jitter,  5,   20)
    loss_c   = _quality_color(loss,  0.5,    2)

    # ── Quality score ─────────────────────────────────────────────── #
    q_score = state.get("quality_score")
    q_label = state.get("quality_label", "N/A")
    q_color = state.get("quality_color", "#888888")

    # ── DNS ──────────────────────────────────────────────────────── #
    dns     = state.get("dns_metrics", {})
    dns_ms  = dns.get("dns_ms")
    dns_tgt = dns.get("target", "")
    dns_c   = _quality_color(dns_ms, 30, 100)

    # ── Gateway ──────────────────────────────────────────────────── #
    gw       = state.get("gateway_metrics", {})
    gw_ip    = gw.get("gateway_ip", "")
    gw_iface = gw.get("interface", "")
    gw_rtt   = gw.get("rtt_avg_ms")
    gw_loss  = gw.get("packet_loss")
    gw_c     = _quality_color(gw_rtt, 5, 20)

    # ── Throughput ────────────────────────────────────────────────── #
    sm          = state.get("slow_metrics", {})
    dl          = sm.get("download_mbps")
    ul          = sm.get("upload_mbps")
    last_tested = sm.get("last_tested", "Never")

    # ── IPv6 ─────────────────────────────────────────────────────── #
    ipv6       = state.get("ipv6", {})
    ipv6_avail = ipv6.get("available")
    ipv6_rtt   = ipv6.get("rtt_ms")
    if ipv6_avail is True:
        ipv6_str = f"{_c('#00e676')}✓ Available"
        if isinstance(ipv6_rtt, (int, float)):
            ipv6_str += f"{_c(DM)} ({ipv6_rtt:.0f} ms)"
    elif ipv6_avail is False:
        ipv6_str = f"{_c('#ff5252')}✗ Not available"
    else:
        ipv6_str = f"{_c(DM)}Checking…"

    # ── IP identity ───────────────────────────────────────────────── #
    ip_info  = state.get("ip_info", {})
    pub_ip   = ip_info.get("ip",      "N/A")
    isp      = ip_info.get("isp",     "N/A")
    asn      = ip_info.get("asn",     "N/A")
    city     = ip_info.get("city",    "")
    country  = ip_info.get("country", "")
    location = ", ".join(p for p in (city, country) if p) or "N/A"

    ip_type        = state.get("ip_type",        "UNCERTAIN")
    ip_type_reason = state.get("ip_type_reason", "")
    last_change    = state.get("last_ip_change",  "No change recorded")

    ip_type_color = {"DYNAMIC": "#ffca28", "LIKELY_STATIC": "#00e676"}.get(
        ip_type, "#888888"
    )

    # ── History ───────────────────────────────────────────────────── #
    h1  = state.get("history_1h",  {})
    h24 = state.get("history_24h", {})

    h1_rtt  = h1.get("rtt_avg")
    h1_loss = h1.get("packet_loss")
    h24_rtt  = h24.get("rtt_avg")
    h24_loss = h24.get("packet_loss")

    # ── Build lines ───────────────────────────────────────────────── #
    SEP = f"{_c('#333333')}────────────────────────────────"

    lines = [
        f"{_c(H)}╔══════════════════════════════╗",
        f"{_c(H)}║  {_c('#ffffff')}NStatus Network Monitor{_c(H)}      ║",
        f"{_c(H)}╚══════════════════════════════╝",
        "",
        # Quality score — prominently at the top
        f"{_c(D)}Quality  {_c(q_color)}{q_label}"
        + (f"  {_c(D)}({q_score}/100)" if q_score is not None else ""),
        f"{_c(D)}Updated  {_c(W)}{ts}",
        f"{_c(D)}Target   {_c(DM)}{target}",
        "",
        # ── QoS ───────────────────────────────────────── #
        _section("QoS Metrics") + "─" * 13,
        f"  {_c(D)}Latency (avg)  {_c(rtt_c)}{_f(rtt, 1, ' ms')}",
        f"  {_c(D)}  min/max      {_c(DM)}{_f(rtt_min, 1)}  /  {_f(rtt_max, 1, ' ms')}",
        f"  {_c(D)}Jitter        {_c(jitter_c)}{_f(jitter, 1, ' ms')}",
        f"  {_c(D)}Packet Loss   {_c(loss_c)}{_f(loss, 1, '%')}",
        f"  {_c(D)}DNS Latency   {_c(dns_c)}{_f(dns_ms, 0, ' ms')}"
        + (f"  {_c(DM)}({dns_tgt})" if dns_tgt else ""),
        "",
        # ── Gateway / LAN ─────────────────────────────── #
        _section("LAN / Gateway") + "─" * 12,
        f"  {_c(D)}Gateway IP    {_c(W)}{gw_ip or 'N/A'}"
        + (f"  {_c(DM)}({gw_iface})" if gw_iface else ""),
        f"  {_c(D)}LAN Latency   {_c(gw_c)}{_f(gw_rtt, 1, ' ms')}",
        f"  {_c(D)}LAN Loss      {_c(_quality_color(gw_loss, 0.5, 2))}{_f(gw_loss, 1, '%')}",
        "",
        # ── Throughput ────────────────────────────────── #
        _section("Throughput") + "─" * 15,
        f"  {_c(D)}Download      {_c('#29b6f6')}{_f(dl, 1, ' Mbps')}",
        f"  {_c(D)}Upload        {_c('#29b6f6')}{_f(ul, 1, ' Mbps')}",
        f"  {_c(D)}Last tested   {_c(DM)}{last_tested}",
        "",
        # ── Historical averages ───────────────────────── #
        _section("History (avg)") + "─" * 11,
        f"  {_c(D)}1 h   RTT {_c(_quality_color(h1_rtt,30,100))}{_f(h1_rtt, 1, ' ms')}"
        f"  {_c(D)}loss {_c(_quality_color(h1_loss, 0.5, 2))}{_f(h1_loss, 1, '%')}",
        f"  {_c(D)}24 h  RTT {_c(_quality_color(h24_rtt,30,100))}{_f(h24_rtt, 1, ' ms')}"
        f"  {_c(D)}loss {_c(_quality_color(h24_loss, 0.5, 2))}{_f(h24_loss, 1, '%')}",
        "",
        # ── Network Identity ──────────────────────────── #
        _section("Network Identity") + "─" * 9,
        f"  {_c(D)}Public IP     {_c(W)}{pub_ip}",
        f"  {_c(D)}ISP           {_c(W)}{isp}",
        f"  {_c(D)}ASN           {_c(DM)}{asn}",
        f"  {_c(D)}Location      {_c(DM)}{location}",
        f"  {_c(D)}IPv6          {ipv6_str}",
        f"  {_c(D)}IP Type       {_c(ip_type_color)}{ip_type}",
        f"  {_c(DM)}              {ip_type_reason}",
        f"  {_c(D)}IP Changed    {_c(DM)}{last_change}",
        "",
        SEP,
    ]
    return "\n".join(lines)


def write_conky_data(conky_file: Path, state: Dict[str, Any]) -> None:
    _atomic_write(conky_file, format_conky_text(state))
    logger.debug("conky_data.txt written")

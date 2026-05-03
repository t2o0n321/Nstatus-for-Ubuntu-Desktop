"""Atomic writers for state.json and the Conky display text file."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

SIMPLE_MODE_FLAG = Path.home() / ".local/share" / "nstatus" / "simple_mode"


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


def _wrap_lv(indent: str, label: str, lc: str, vc: str, value: str, max_line: int = 38) -> list:
    """Format a label-value pair, wrapping value to a continuation line if it is too wide."""
    avail = max_line - len(indent) - len(label)
    if len(value) <= avail:
        return [f"{_c(lc)}{indent}{label}{_c(vc)}{value}"]
    cut = value.rfind(" ", 0, avail + 1)
    if cut <= 0:
        cut = avail
    cont = " " * (len(indent) + len(label))
    return [
        f"{_c(lc)}{indent}{label}{_c(vc)}{value[:cut]}",
        f"{_c(vc)}{cont}{value[cut + 1:]}",
    ]


# ------------------------------------------------------------------ #
# Conky display text                                                   #
# ------------------------------------------------------------------ #

W  = "#cccccc"   # regular value
D  = "#555555"   # dim / label
H  = "#7986cb"   # section header (indigo)
DM = "#888888"   # medium dim


def _section(title: str) -> str:
    return f"{_c(H)}── {title} "


def _mode_button(simple: bool) -> str:
    """Toggle bar shown under the title box. Active mode is highlighted."""
    if simple:
        return f"  {_c('#555555')}[○ Full]  {_c('#00e676')}[● Simple]"
    return f"  {_c(H)}[● Full]  {_c(D)}[○ Simple]"


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

    # ── Cloudflare endpoints ──────────────────────────────────────── #
    cf_endpoints: list = state.get("cloudflare_endpoints", [])

    # ── Build lines ───────────────────────────────────────────────── #
    SEP = f"{_c('#333333')}────────────────────────────────"

    lines = [
        f"  {_c(H)}╔{'═' * 26}╗",
        f"  {_c(H)}║  {_c('#ffffff')}NStatus Network Monitor{_c(H)} ║",
        f"  {_c(H)}╚{'═' * 26}╝",
        "",
        "",
        # Quality score — prominently at the top
        f"  {_c(D)}Quality       {_c(q_color)}{q_label}"
        + (f"  {_c(D)}({q_score}/100)" if q_score is not None else ""),
        f"  {_c(D)}Updated       {_c(W)}{ts}",
        f"  {_c(D)}Target        {_c(DM)}{target}",
        "",
        # ── QoS ───────────────────────────────────────── #
        _section("QoS Metrics") + "─" * 13,
        f"  {_c(D)}Latency (avg) {_c(rtt_c)}{_f(rtt, 1, ' ms')}",
        f"  {_c(D)}  min/max     {_c(DM)}{_f(rtt_min, 1)}  /  {_f(rtt_max, 1, ' ms')}",
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
        *_wrap_lv("  ", "ISP           ", D, W, isp),
        f"  {_c(D)}ASN           {_c(DM)}{asn}",
        f"  {_c(D)}Location      {_c(DM)}{location}",
        f"  {_c(D)}IPv6          {ipv6_str}",
        f"  {_c(D)}IP Type       {_c(ip_type_color)}{ip_type}",
        f"{' ' * 16}{_c(DM)}{ip_type_reason}",
        f"  {_c(D)}IP Changed    {_c(DM)}{last_change}",
        "",
    ]

    # ── Cloudflare section (only rendered if endpoints configured) ─── #
    if cf_endpoints:
        lines.append(_section("Cloudflare Services") + "─" * 6)
        for ep in cf_endpoints:
            name         = ep.get("name", ep.get("url", "?"))
            http_status  = ep.get("http_status", 0)
            is_up        = ep.get("is_up", False)
            is_cf        = ep.get("is_cloudflare", False)
            cf_ray       = ep.get("cf_ray", "")
            pop_code     = ep.get("pop_code", "")
            pop_city     = ep.get("pop_city", "")
            cache_status = ep.get("cache_status", "")
            ttfb_ms      = ep.get("ttfb_ms")
            total_ms     = ep.get("total_ms")
            tls_ms       = ep.get("tls_ms")
            uptime_24h   = ep.get("uptime_24h")
            checked_at   = ep.get("checked_at", "")
            error_msg    = ep.get("error_msg", "")
            cf_error_msg = ep.get("cf_error_msg", "")

            # Status color
            if http_status == 0:
                status_c = "#ff5252"
                status_s = "✗ No response"
            elif is_up:
                status_c = "#00e676"
                status_s = f"✓ {http_status} OK"
            elif 400 <= http_status < 500:
                status_c = "#ffca28"
                status_s = f"⚠ {http_status} Client Err"
            else:
                status_c = "#ff5252"
                status_s = f"✗ {http_status}"
                if cf_error_msg:
                    status_s += f" ({cf_error_msg})"

            # Cache badge
            cache_color = {
                "HIT":      "#00e676",
                "MISS":     "#ffca28",
                "DYNAMIC":  "#888888",
                "BYPASS":   "#ff9800",
                "EXPIRED":  "#ff9800",
                "STALE":    "#ff9800",
            }.get(cache_status, "#555555")
            cache_badge = f"  {_c(cache_color)}[{cache_status}]" if cache_status else ""

            # PoP display
            pop_str = pop_code
            if pop_city and pop_city != pop_code:
                pop_str = f"{pop_code} {_c(DM)}({pop_city})"

            # Uptime badge
            if uptime_24h is not None:
                u_color = "#00e676" if uptime_24h >= 99 else "#ffca28" if uptime_24h >= 95 else "#ff5252"
                uptime_str = f"{_c(u_color)}{uptime_24h:.1f}%"
            else:
                uptime_str = f"{_c(DM)}—"

            # Cloudflare shield marker
            cf_shield = f"{_c('#f6821f')} ☁CF" if is_cf else f"{_c(DM)} (no CF)"

            lines += [
                f"  {_c(W)}{name}{cf_shield}",
                f"    {_c(D)}Status   {_c(status_c)}{status_s}{cache_badge}",
                f"    {_c(D)}TTFB     {_c('#29b6f6')}{_f(ttfb_ms, 0, ' ms')}"
                f"  {_c(D)}Total {_c(DM)}{_f(total_ms, 0, ' ms')}",
                f"    {_c(D)}TLS      {_c(DM)}{_f(tls_ms, 0, ' ms')}"
                f"  {_c(D)}PoP {_c(W)}{pop_str}",
                f"    {_c(D)}Uptime   {uptime_str}{_c(DM)} (24 h)"
                + (f"  {_c(DM)}@ {checked_at}" if checked_at else ""),
            ]
            if error_msg:
                lines.append(f"    {_c('#ff5252')}⚠ {error_msg}")
            lines.append("")

    lines.append(SEP)
    return "\n".join(lines)


def format_simple_conky_text(state: Dict[str, Any]) -> str:
    """Compact view: Quality, Updated, Public IP, IP Type only."""
    ts = state.get("updated_at", "---")

    q_score = state.get("quality_score")
    q_label = state.get("quality_label", "N/A")
    q_color = state.get("quality_color", "#888888")

    ip_info      = state.get("ip_info", {})
    pub_ip       = ip_info.get("ip", "N/A")
    ip_type      = state.get("ip_type", "UNCERTAIN")
    ip_type_color = {"DYNAMIC": "#ffca28", "LIKELY_STATIC": "#00e676"}.get(
        ip_type, "#888888"
    )

    SEP = f"{_c('#333333')}────────────────────────────────"

    lines = [
        f"  {_c(H)}╔{'═' * 26}╗",
        f"  {_c(H)}║  {_c('#ffffff')}NStatus Network Monitor{_c(H)} ║",
        f"  {_c(H)}╚{'═' * 26}╝",
        "",
        "",
        f"  {_c(D)}Quality       {_c(q_color)}{q_label}"
        + (f"  {_c(D)}({q_score}/100)" if q_score is not None else ""),
        f"  {_c(D)}Updated       {_c(W)}{ts}",
        "",
        f"  {_c(D)}Public IP     {_c(W)}{pub_ip}",
        f"  {_c(D)}IP Type       {_c(ip_type_color)}{ip_type}",
        "",
        SEP,
    ]
    return "\n".join(lines)


def write_conky_data(conky_file: Path, state: Dict[str, Any]) -> None:
    simple = SIMPLE_MODE_FLAG.exists()
    text   = format_simple_conky_text(state) if simple else format_conky_text(state)
    _atomic_write(conky_file, text)
    logger.debug("conky_data.txt written (simple=%s)", simple)

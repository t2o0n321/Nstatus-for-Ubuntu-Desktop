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
        with os.fdopen(fd, "w") as fh:
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
    """Persist the full state dict as pretty-printed JSON."""
    content = json.dumps(state, indent=2, default=str)
    _atomic_write(state_file, content)
    logger.debug("state.json written")


# ------------------------------------------------------------------ #
# Conky display text                                                   #
# ------------------------------------------------------------------ #

def _color(hex_color: str) -> str:
    """Emit a Conky color tag."""
    return f"${{color {hex_color}}}"


def _quality_color(value: Any, good_thresh: float, warn_thresh: float) -> str:
    """
    Return a hex color string representing signal quality:
      ≤ good_thresh  →  green
      ≤ warn_thresh  →  yellow
      >  warn_thresh →  red
    """
    if not isinstance(value, (int, float)):
        return "#888888"
    if value <= good_thresh:
        return "#00e676"
    if value <= warn_thresh:
        return "#ffca28"
    return "#ff5252"


def _fmt_float(val: Any, decimals: int = 1, unit: str = "") -> str:
    if isinstance(val, (int, float)):
        return f"{val:.{decimals}f}{unit}"
    return "N/A"


def format_conky_text(state: Dict[str, Any]) -> str:
    ts = state.get("updated_at", "---")

    # ── fast metrics ──────────────────────────────────────────────── #
    fm = state.get("fast_metrics", {})
    rtt    = fm.get("rtt_avg")
    jitter = fm.get("jitter")
    loss   = fm.get("packet_loss")
    target = fm.get("target", "?")

    rtt_str    = _fmt_float(rtt,    1, " ms")
    jitter_str = _fmt_float(jitter, 1, " ms")
    loss_str   = _fmt_float(loss,   1, "%")

    rtt_c    = _quality_color(rtt,    30,  100)
    jitter_c = _quality_color(jitter,  5,   20)
    loss_c   = _quality_color(loss,    0.5,  2)

    # ── slow metrics ──────────────────────────────────────────────── #
    sm = state.get("slow_metrics", {})
    dl          = sm.get("download_mbps")
    ul          = sm.get("upload_mbps")
    last_tested = sm.get("last_tested", "Never")

    dl_str = _fmt_float(dl, 1, " Mbps")
    ul_str = _fmt_float(ul, 1, " Mbps")

    # ── IP info ───────────────────────────────────────────────────── #
    ip_info  = state.get("ip_info", {})
    pub_ip   = ip_info.get("ip",      "N/A")
    isp      = ip_info.get("isp",     "N/A")
    asn      = ip_info.get("asn",     "")
    city     = ip_info.get("city",    "")
    country  = ip_info.get("country", "")
    location = ", ".join(p for p in (city, country) if p) or "N/A"

    ip_type        = state.get("ip_type", "UNCERTAIN")
    ip_type_reason = state.get("ip_type_reason", "")
    last_change    = state.get("last_ip_change", "No change recorded")

    type_color_map = {
        "DYNAMIC":       "#ffca28",
        "LIKELY_STATIC": "#00e676",
        "UNCERTAIN":     "#888888",
    }
    ip_type_c = type_color_map.get(ip_type, "#888888")

    W = "#cccccc"   # label white
    D = "#555555"   # dim gray

    lines = [
        f"{_color('#7986cb')}╔══════════════════════════════╗",
        f"{_color('#7986cb')}║  {_color('#ffffff')}NStatus Network Monitor{_color('#7986cb')}      ║",
        f"{_color('#7986cb')}╚══════════════════════════════╝",
        "",
        f"{_color(D)}Updated : {_color(W)}{ts}",
        f"{_color(D)}Target  : {_color(D)}{target}",
        "",
        f"{_color('#7986cb')}── QoS ─────────────────────────",
        f"  {_color(D)}Latency    {_color(rtt_c)}{rtt_str:<12}{_color(D)}(avg RTT)",
        f"  {_color(D)}Jitter     {_color(jitter_c)}{jitter_str:<12}{_color(D)}(mean dev)",
        f"  {_color(D)}Pkt Loss   {_color(loss_c)}{loss_str}",
        "",
        f"{_color('#7986cb')}── Throughput ───────────────────",
        f"  {_color(D)}Download   {_color('#29b6f6')}{dl_str}",
        f"  {_color(D)}Upload     {_color('#29b6f6')}{ul_str}",
        f"  {_color(D)}Tested     {_color(D)}{last_tested}",
        "",
        f"{_color('#7986cb')}── Network Identity ─────────────",
        f"  {_color(D)}Public IP  {_color(W)}{pub_ip}",
        f"  {_color(D)}ISP        {_color(W)}{isp}",
        f"  {_color(D)}ASN        {_color(D)}{asn if asn else 'N/A'}",
        f"  {_color(D)}Location   {_color(D)}{location}",
        f"  {_color(D)}IP Type    {_color(ip_type_c)}{ip_type}",
        f"  {_color(D)}           {_color(D)}{ip_type_reason}",
        f"  {_color(D)}Changed    {_color(D)}{last_change}",
        "",
        f"{_color('#333333')}────────────────────────────────",
    ]
    return "\n".join(lines)


def write_conky_data(conky_file: Path, state: Dict[str, Any]) -> None:
    """Write the Conky-markup display text atomically."""
    _atomic_write(conky_file, format_conky_text(state))
    logger.debug("conky_data.txt written")

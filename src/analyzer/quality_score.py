"""Composite network quality score (0 – 100)."""

from typing import Any, Optional


def compute_quality_score(
    rtt_avg: Any,
    jitter: Any,
    packet_loss: Any,
    dns_ms: Any = None,
    gateway_rtt: Any = None,
) -> int:
    """
    Compute a single 0-100 quality score from network QoS metrics.

    Scoring weights
    ───────────────
    Packet loss  40 pts  — dominates because even 1 % loss is very noticeable
    RTT (avg)    30 pts  — latency affects all interactive traffic
    Jitter       20 pts  — variation degrades VoIP / video calls
    DNS latency  10 pts  — slow DNS makes every new connection feel sluggish

    Thresholds are chosen for a typical consumer broadband connection.
    """
    score = 100.0

    # ── Packet loss (0 → 10 %) — 40 points ──────────────────────── #
    if isinstance(packet_loss, (int, float)):
        # 0 % → 0 deducted; 10 %+ → full 40 deducted
        score -= min(packet_loss * 4.0, 40.0)

    # ── RTT (0 → 200 ms) — 30 points ────────────────────────────── #
    if isinstance(rtt_avg, (int, float)):
        # 0 ms → 0; 200 ms+ → full 30 deducted
        score -= min(rtt_avg / 200 * 30, 30.0)

    # ── Jitter (0 → 50 ms) — 20 points ──────────────────────────── #
    if isinstance(jitter, (int, float)):
        # 0 ms → 0; 50 ms+ → full 20 deducted
        score -= min(jitter / 50 * 20, 20.0)

    # ── DNS latency (0 → 200 ms) — 10 points ────────────────────── #
    if isinstance(dns_ms, (int, float)):
        # 0 ms → 0; 200 ms+ → full 10 deducted
        score -= min(dns_ms / 200 * 10, 10.0)

    return max(0, min(100, int(score)))


def score_label(score: int) -> str:
    """Return a human-readable label for a quality score."""
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Fair"
    if score >= 40:
        return "Poor"
    return "Bad"


def score_color(score: int) -> str:
    """Return a Conky hex color appropriate for the given score."""
    if score >= 90:
        return "#00e676"   # green
    if score >= 75:
        return "#69f0ae"   # light green
    if score >= 60:
        return "#ffca28"   # yellow
    if score >= 40:
        return "#ff9800"   # orange
    return "#ff5252"       # red

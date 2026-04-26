"""Compute RTT statistics and jitter from raw ping samples."""

from dataclasses import dataclass
from typing import List


@dataclass
class PingStats:
    rtt_avg: float
    rtt_min: float
    rtt_max: float
    rtt_mdev: float   # standard deviation
    jitter: float     # RFC-3550 mean-deviation of consecutive RTTs
    packet_loss: float
    samples: int      # packets received


def compute_ping_stats(rtts: List[float], sent: int) -> PingStats:
    """
    Derive all QoS metrics from a list of round-trip time samples.

    Args:
        rtts:  Measured RTT values in milliseconds (only received packets).
        sent:  Total packets transmitted (for loss calculation).
    """
    received = len(rtts)
    packet_loss = ((sent - received) / max(sent, 1)) * 100.0

    if not rtts:
        return PingStats(
            rtt_avg=0.0,
            rtt_min=0.0,
            rtt_max=0.0,
            rtt_mdev=0.0,
            jitter=0.0,
            packet_loss=round(packet_loss, 1),
            samples=0,
        )

    n = len(rtts)
    rtt_avg  = sum(rtts) / n
    rtt_min  = min(rtts)
    rtt_max  = max(rtts)

    # Population standard deviation
    variance = sum((r - rtt_avg) ** 2 for r in rtts) / n
    rtt_mdev = variance ** 0.5

    # Jitter: mean of |RTT[i] - RTT[i-1]| across consecutive pairs (RFC 3550)
    if n >= 2:
        jitter = sum(abs(rtts[i] - rtts[i - 1]) for i in range(1, n)) / (n - 1)
    else:
        jitter = 0.0

    return PingStats(
        rtt_avg=round(rtt_avg, 2),
        rtt_min=round(rtt_min, 2),
        rtt_max=round(rtt_max, 2),
        rtt_mdev=round(rtt_mdev, 2),
        jitter=round(jitter, 2),
        packet_loss=round(packet_loss, 1),
        samples=received,
    )

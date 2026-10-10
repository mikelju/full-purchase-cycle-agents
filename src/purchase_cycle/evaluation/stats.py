"""Confidence intervals and paired tests without extra libraries."""

from math import comb, sqrt

Z_95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(lost: int, gained: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pairs.

    `lost` counts cases right in the baseline and wrong now; `gained` the reverse.
    """
    n = lost + gained
    if n == 0:
        return 1.0
    k = min(lost, gained)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def target_cells(value: float, target: float, op: str = ">=") -> tuple[str, str]:
    """Target fixed before measuring and whether the value meets it, apart from the gate result."""
    met = value >= target - 1e-9 if op == ">=" else value <= target + 1e-9
    return f"{op}{target * 100:.1f}%", "met" if met else "not met"


def zero_event_note(s: dict) -> str:
    """Wilson upper bound of a rate with zero events, printed after its row."""
    return f"  0 of {s['n']}, Wilson upper bound {s['high'] * 100:.1f}%" if s["n"] and s["hits"] == 0 else ""

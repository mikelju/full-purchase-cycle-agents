"""Wilson intervals and exact McNemar test against known values."""

import pytest

from purchase_cycle.evaluation.stats import mcnemar_exact, wilson_interval


def test_wilson_matches_reference_values():
    # Reference: 81 successes in 263 trials, Wilson 95% interval [0.2553, 0.3662].
    low, high = wilson_interval(81, 263)
    assert low == pytest.approx(0.2553, abs=1e-4)
    assert high == pytest.approx(0.3662, abs=1e-4)
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == pytest.approx(1.0)


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 5) == 1.0
    # 10 discordant pairs all against: 2 * 0.5**10
    assert mcnemar_exact(10, 0) == pytest.approx(2 / 1024)
    assert mcnemar_exact(8, 2) == pytest.approx(0.109375)

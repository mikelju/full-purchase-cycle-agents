"""C21 (phase 05, deviation 05.1): the `order_scenarios` suite, its versioned scenario files and its report."""

from collections import Counter

from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.evaluation import scenarios_eval as se


def test_the_scenario_files_meet_the_c21_minimums():
    items = se.load_dataset()
    tags = Counter(tag for item in items for tag in item["tags"])
    channels = Counter(item["channel"] for item in items)
    minimums = se.MINIMUMS
    assert len(items) >= minimums["scenarios"]
    assert set(channels) == set(se.CHANNELS)
    assert min(channels.values()) >= minimums["per_channel"]
    for tag in se.TAGS:
        assert tags[tag] >= minimums[tag], tag


def test_the_scenario_files_are_the_versioned_build():
    assert se.load_dataset() == se.build_items()


def test_each_scenario_is_consistent_with_its_tags():
    prices = {p.sku: p.price_eur for p in PRODUCTS}
    codes = {c.code for c in CUSTOMERS}
    for item in se.load_dataset():
        tags, steps = item["tags"], item["steps"]
        assert item["customer_code"] in codes
        assert steps[0] == "deliver"
        assert (
            steps.count("answer")
            == len(item["answers"])
            == (2 if "two_answers" in tags else int("clarification" in tags))
        )
        assert ("redeliver" in steps) == ("redelivery" in tags)
        assert (item["crash_at"] is not None) == ("resume" in steps) == ("crash_resume" in tags)
        assert ("unknown_product" in tags) == any(r["sku"] is None for r in item["requested"])
        assert (item["expected"]["clarification"] == "answered") == ("clarification" in tags)
        for line in item["expected"]["lines"]:
            assert prices[line["sku"]] == line["price_eur"]

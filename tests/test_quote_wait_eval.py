"""C11 (phase 06): the deterministic `quote_wait` evaluation, its scripted timelines, grader, report and gate."""

from collections import Counter

from purchase_cycle.evaluation import quote_wait_eval as qw
from purchase_cycle.suppliers import SUPPLIER_FAMILIES

FAMILY = {p.sku: p.family for p in qw.PRODUCTS}


def _suppliers(sku):
    return sorted(code for code, family in SUPPLIER_FAMILIES if family == FAMILY[sku])


# Increment 11: the scripted timelines (C11)


def test_the_timelines_meet_the_coverage_minimums():
    items = qw.load_dataset()
    tags = Counter(tag for item in items for tag in item["tags"])
    assert len(items) >= qw.MINIMUMS["timelines"] == 20
    assert len({i["id"] for i in items}) == len(items)
    for tag in qw.TAGS:
        assert tags[tag] >= qw.MINIMUMS[tag] == 3, tag


def test_the_timeline_files_are_the_versioned_build():
    assert qw.load_dataset() == qw.build_items()


def test_each_timeline_is_consistent_with_its_tags():
    for item in qw.load_dataset():
        tags, steps, requests = item["tags"], item["steps"], item["expected"]["requests"]
        ops = [s["op"] for s in steps]
        assert set(ops) <= set(qw.OPS), item["id"]
        assert ("rescan" in tags) == (ops.count("scan") >= 2), item["id"]
        assert ("repeated_tick" in tags) == any(a == b == "tick" for a, b in zip(ops, ops[1:], strict=False)), item[
            "id"
        ]
        assert ("restart" in tags) == any(s.get("new_process") for s in steps), item["id"]
        replies = Counter((r["reference"], r["supplier"]) for s in steps if s["op"] == "receive" for r in s["replies"])
        assert ("duplicate_reply" in tags) == any(n > 1 for n in replies.values()), item["id"]
        assert ("all_replied" in tags) == any(r["replied"] == _suppliers(r["sku"]) for r in requests), item["id"]
        assert ("none_replied" in tags) == any(r["status"] == "no_offers" for r in requests), item["id"]
        some = [r for r in requests if r["status"] == "ready_for_decision" and r["replied"] != _suppliers(r["sku"])]
        assert ("some_replied" in tags) == bool(some), item["id"]
        assert item["expected"]["failures"] == sum(r["status"] == "no_offers" for r in requests), item["id"]
        for request in requests:
            assert set(request["replied"]) | set(request["reminded"]) <= set(_suppliers(request["sku"]))

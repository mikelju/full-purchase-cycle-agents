"""C11 (phase 06): the deterministic `quote_wait` evaluation, its scripted timelines, grader, report and gate."""

import copy
from collections import Counter

import pytest

from purchase_cycle import cli
from purchase_cycle.evaluation import harness
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


# Increment 12: runner, grader, critical errors, report and gate (C11, C1)


def _one_timeline(tmp_path, item_id="QW-004"):
    """QW-004: order, scan, a reply from SUP-002, a reminder tick to SUP-001 and SUP-003 and the deadline tick."""
    item = next(i for i in qw.load_dataset() if i["id"] == item_id)
    qw.write_items([item], tmp_path / "timelines")
    return item, tmp_path / "timelines"


def _second_open_request(before, after, ticks):
    after["requests"]["QR-0099"] = dict(after["requests"]["QR-0001"])


def _request_without_shortfall(before, after, ticks):
    sku = next(p.sku for p in qw.PRODUCTS if p.sku not in before["shortfall"])
    after["requests"]["QR-0099"] = {**after["requests"]["QR-0001"], "sku": sku, "quantity": 5}


def _wrong_quantity(before, after, ticks):
    after["requests"]["QR-0001"]["quantity"] += 1


def _reminder_after_reply(before, after, ticks):
    after["outbox"].add("QR-0001-reminder-SUP-002.eml")


def _repeated_reminder(before, after, ticks):
    after["requests"]["QR-0001"]["reminded"]["SUP-001"] = "2099-01-01 00:00:00"


def _email_after_close(before, after, ticks):
    before["requests"]["QR-0001"]["status"] = "ready_for_decision"
    after["outbox"].add("QR-0001-request-SUP-009.eml")


def _waiting_after_deadline(before, after, ticks):
    after["requests"]["QR-0001"]["status"] = "waiting"


# (step op, tick number or None, mutation of the step snapshots); the thread kind is injected at the end.
INJECTIONS = {
    "second_open_request": ("scan", None, _second_open_request),
    "request_without_shortfall": ("scan", None, _request_without_shortfall),
    "wrong_quantity": ("scan", None, _wrong_quantity),
    "reminder_after_reply": ("tick", 1, _reminder_after_reply),
    "repeated_reminder": ("tick", 2, _repeated_reminder),
    "email_after_close": ("tick", 2, _email_after_close),
    "waiting_after_deadline": ("tick", 2, _waiting_after_deadline),
    "lost_or_duplicated_thread": (None, None, None),
}


def test_every_quote_wait_kind_has_an_injection():
    assert tuple(INJECTIONS) == qw.CRITICAL_KINDS


def test_a_clean_timeline_passes_with_no_critical_error(tmp_path, capsys, no_network):
    _, folder = _one_timeline(tmp_path)
    assert qw.evaluate("replay", "test", dataset_dir=folder) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("scenario_success "))
    assert row.split()[1] == "100.0%" and "PASS" in row
    row = next(line for line in out.splitlines() if line.startswith("critical_errors "))
    assert row.split()[1] == "0" and row.endswith("PASS")
    assert "by kind: second_open_request=0" in out
    assert "failed scenarios  0 of 1, Wilson upper bound" in out
    assert no_network == []


@pytest.mark.parametrize("kind", qw.CRITICAL_KINDS)
def test_each_injected_critical_error_fails_quote_wait_with_exit_one(kind, tmp_path, capsys, monkeypatch, no_network):
    item, folder = _one_timeline(tmp_path)
    op, tick_number, mutate = INJECTIONS[kind]
    real_step, real_threads = qw._step_errors, qw._thread_errors
    ticks = []

    def step_errors(before, after, step):
        before, after = copy.deepcopy(before), copy.deepcopy(after)
        ticks.extend([1] if step["op"] == "tick" else [])
        if step["op"] == op and (tick_number is None or len(ticks) == tick_number):
            mutate(before, after, ticks)
        return real_step(before, after, step)

    def thread_errors(requests, threads):
        threads = {t: n for t, n in threads.items() if t != "quote-QR-0001"}
        return real_threads(requests, threads)

    monkeypatch.setattr(qw, "_step_errors", step_errors)
    if op is None:
        monkeypatch.setattr(qw, "_thread_errors", thread_errors)
    assert qw.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert f"{kind}=1" in out
    assert f"  {item['id']}  critical errors {{'{kind}': 1}}" in out
    assert "GATE FAILED: critical_errors 1 above threshold 0" in out


def test_a_wrong_expectation_fails_quote_wait_with_exit_one(tmp_path, capsys, no_network):
    item, folder = _one_timeline(tmp_path)
    item["expected"]["requests"][0]["status"] = "no_offers"
    qw.write_items([item], folder)
    assert qw.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: scenario_success 0.0% below threshold 100.0%" in out
    assert f"  {item['id']}  expected" in out


def test_quote_wait_is_a_local_suite():
    assert "quote_wait" in harness.SUITES and "quote_wait" in harness.ALL_SUITES
    assert "quote_wait" not in harness.UPLOAD_SUITES
    assert harness._suite("quote_wait") is qw


def test_the_quote_wait_suite_passes_through_the_cli(capsys, no_network):
    assert cli.main(["eval", "--suite", "quote_wait"]) == 0
    out = capsys.readouterr().out
    assert f"quote_wait  mode=replay  scenarios={len(qw.load_dataset())}" in out
    for tag in qw.TAGS:
        assert f"  {tag:<16} 100.0%" in out
    assert no_network == []

"""C11 (phase 10): the deterministic `goods_receipt` evaluation, its scenarios, grader, report and gate."""

import copy
from collections import Counter

import pytest

from purchase_cycle.evaluation import goods_receipt_eval as gr
from purchase_cycle.evaluation import harness


def test_the_suite_is_registered_for_eval():
    assert "goods_receipt" in harness.SUITES
    assert harness._suite("goods_receipt") is gr


def test_the_scenarios_meet_the_coverage_minimums():
    items = gr.load_dataset()
    tags = Counter(tag for item in items for tag in item["tags"])
    assert len(items) >= gr.MINIMUMS["scenarios"] == 30
    assert len({i["id"] for i in items}) == len(items)
    for tag in gr.TAGS:
        assert tags[tag] >= gr.MINIMUMS[tag] == 2, tag


def test_the_scenario_files_are_the_versioned_build():
    assert gr.load_dataset() == gr.build_items()


def test_each_scenario_is_consistent_with_its_tags():
    for item in gr.load_dataset():
        assert item["tags"] == gr.derive_tags(item), item["id"]
        assert {s["op"] for s in item["steps"]} <= set(gr.OPS), item["id"]


def _one(tmp_path, item_id="GR-001"):
    item = next(i for i in gr.load_dataset() if i["id"] == item_id)
    gr.write_items([item], tmp_path / "scenarios")
    return item, tmp_path / "scenarios"


def test_a_clean_scenario_passes_with_no_critical_error(tmp_path, capsys, no_network):
    _, folder = _one(tmp_path)
    assert gr.evaluate("replay", "test", dataset_dir=folder) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("scenario_success "))
    assert row.split()[1] == "100.0%" and "PASS" in row
    row = next(line for line in out.splitlines() if line.startswith("critical_errors "))
    assert row.split()[1] == "0" and row.endswith("PASS")
    assert no_network == []


def test_the_whole_suite_passes(capsys, no_network):
    assert gr.evaluate("replay", "test") == 0
    out = capsys.readouterr().out
    assert f"scenarios={len(gr.load_dataset())}" in out
    for tag in gr.TAGS:
        assert f"  {tag:<24} 100.0%" in out


# One injection per critical error: each mutates what the grader reads, not the module under test.


def _negative_stock(final):
    final["stock"][gr.X] = -1


def _stock_mismatch(final):
    final["stock"][gr.X] += 1


def _receipt_twice(final):
    final["receipts"].append(final["receipts"][0])


def _receipt_not_candidate(final):
    final["receipts"].append(99)


def _served_twice(final):
    final["notes"].append(copy.deepcopy(final["notes"][0]))


def _not_covered(final):
    final["steps"][0][
        "received"
    ] = []  # the oracle credits no units, so the order served is not covered, stock stays >= 0


def _lines_differ(final):
    final["notes"][0]["lines"].append([gr.X, 1])


def _out_of_order(final):
    final["steps"][0]["served"].reverse()


def _skipped_coverable(final):
    final["steps"][0]["served"] = [2, 3]  # the pass skips order 1, which the stock covers, and serves 2 and 3


def _po_quantity_received(final):
    final["stock"][gr.A] = 100  # receive recorded the purchase order quantity, not the 60 delivered


def _file_without_row(final):
    final["files"].append("CDN-0099.txt")


def _row_without_file(final):
    final["notes"][0]["file_exists"] = False


def _model_call(final):
    final["calls"] += 1


# (injection, scenario): GR-026 serves three orders in one run, GR-001 receives one note and serves one order.
INJECTIONS = {
    "negative_stock": (_negative_stock, "GR-001", "negative_stock"),
    "stock_change_mismatch": (_stock_mismatch, "GR-001", "stock_change_mismatch"),
    "note_received_twice": (_receipt_twice, "GR-001", "receipt_repeated_or_not_candidate"),
    "non_candidate_received": (_receipt_not_candidate, "GR-001", "receipt_repeated_or_not_candidate"),
    "order_served_twice": (_served_twice, "GR-001", "order_served_twice_or_two_notes"),
    "served_not_covered": (_not_covered, "GR-001", "served_not_covered"),
    "note_lines_differ": (_lines_differ, "GR-001", "note_lines_differ"),
    "served_out_of_order": (_out_of_order, "GR-026", "order_out_of_arrival_order"),
    "coverable_order_skipped": (_skipped_coverable, "GR-026", "order_out_of_arrival_order"),
    "po_quantity_received": (_po_quantity_received, "GR-015", "stock_change_mismatch"),
    "file_without_row": (_file_without_row, "GR-001", "file_row_mismatch"),
    "row_without_file": (_row_without_file, "GR-001", "file_row_mismatch"),
    "model_call": (_model_call, "GR-001", "model_call"),
}


def test_every_critical_kind_has_an_injection():
    assert {kind for _, _, kind in INJECTIONS.values()} == set(gr.CRITICAL_KINDS)


@pytest.mark.parametrize("name", INJECTIONS)
def test_each_injected_error_fails_goods_receipt_with_exit_one(name, tmp_path, capsys, monkeypatch, no_network):
    injection, item_id, kind = INJECTIONS[name]
    item, folder = _one(tmp_path, item_id)
    real_errors = gr._errors

    def errors(it, final):
        final = copy.deepcopy(final)
        injection(final)
        return real_errors(it, final)

    monkeypatch.setattr(gr, "_errors", errors)
    assert gr.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert f"{kind}=" in out and f"{kind}=0" not in out
    assert f"  {item['id']}  critical errors" in out
    assert "GATE FAILED: critical_errors" in out


def test_a_wrong_expectation_fails_goods_receipt_with_exit_one(tmp_path, capsys, no_network):
    item, folder = _one(tmp_path)
    item["expected"]["orders"] = ["received"]
    gr.write_items([item], folder)
    assert gr.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: scenario_success 0.0% below threshold 100.0%" in out
    assert f"  {item['id']}  expected" in out


def test_a_wrong_printed_line_fails_the_scenario(tmp_path, capsys, no_network):
    item, folder = _one(tmp_path)
    item["steps"][0]["out"] = ["nothing changed"]
    gr.write_items([item], folder)
    assert gr.evaluate("replay", "test", dataset_dir=folder) == 1
    assert f"  {item['id']}  expected" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["served_not_covered", "coverable_order_skipped", "po_quantity_received"])
def test_the_independent_checks_fire_on_their_own(name, tmp_path, monkeypatch, no_network):
    injection, item_id, kind = INJECTIONS[name]
    item = next(i for i in gr.load_dataset() if i["id"] == item_id)
    seen = []
    real_errors = gr._errors
    monkeypatch.setattr(gr, "_errors", lambda it, final: seen.append(final) or real_errors(it, final))
    assert not any(gr.run_item(item, tmp_path)["critical"].values())
    ran = copy.deepcopy(seen[0])
    injection(ran)
    found = gr._errors(item, ran)
    assert found[kind] > 0
    assert found["negative_stock"] == 0

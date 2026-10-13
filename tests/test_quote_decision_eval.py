"""C14 (phase 07): the deterministic `quote_decision` evaluation, its scenarios, grader, report and gate."""

import copy
from collections import Counter

import pytest

from purchase_cycle import cli
from purchase_cycle.evaluation import harness
from purchase_cycle.evaluation import quote_decision_eval as qd


def test_the_scenarios_meet_the_coverage_minimums():
    items = qd.load_dataset()
    tags = Counter(tag for item in items for tag in item["tags"])
    assert len(items) >= qd.MINIMUMS["scenarios"] == 24
    assert len({i["id"] for i in items}) == len(items)
    for tag in qd.TAGS:
        assert tags[tag] >= qd.MINIMUMS[tag] == 3, tag


def test_the_scenario_files_are_the_versioned_build():
    assert qd.load_dataset() == qd.build_items()


def test_each_scenario_is_consistent_with_its_tags_and_the_oracle():
    for item in qd.load_dataset():
        assert item["tags"] == qd.derive_tags(item), item["id"]
        assert {s["op"] for s in item["steps"]} <= set(qd.OPS), item["id"]
        expected = item["expected"]
        if item["request"]["status"] == "ready_for_decision":
            assert expected["recommended"] == qd.oracle_winner(item), item["id"]
            assert (expected["rule"] == "none") == (expected["recommended"] is None), item["id"]
        else:
            assert expected["recommended"] is None and expected["rule"] is None, item["id"]
        for order in expected["orders"]:
            assert order["supplier"] in {r["supplier"] for r in item["replies"]}, item["id"]


def _one(tmp_path, item_id="QD-001"):
    item = next(i for i in qd.load_dataset() if i["id"] == item_id)
    qd.write_items([item], tmp_path / "scenarios")
    return item, tmp_path / "scenarios"


def test_a_clean_scenario_passes_with_no_critical_error(tmp_path, capsys, no_network):
    _, folder = _one(tmp_path)
    assert qd.evaluate("replay", "test", dataset_dir=folder) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("scenario_success "))
    assert row.split()[1] == "100.0%" and "PASS" in row
    row = next(line for line in out.splitlines() if line.startswith("critical_errors "))
    assert row.split()[1] == "0" and row.endswith("PASS")
    assert "by kind: multiple_orders=0" in out
    assert no_network == []


# One injection per critical error: each mutates what the grader reads, not the module under test.


def _multiple_orders(final):
    final["orders"].append(dict(final["orders"][0]))


def _order_without_approval(final):
    final["status"] = "rejected"


def _order_from_unusable_offer(final):
    final["offers"]["SUP-001"]["usable"] = 0


def _order_differs(final):
    final["orders"][0]["quantity"] += 1


def _status_mismatch(final):
    final["status"] = "awaiting_approval"


FINAL_INJECTIONS = {
    "multiple_orders": _multiple_orders,
    "order_without_approval": _order_without_approval,
    "order_from_unusable_offer": _order_from_unusable_offer,
    "order_differs_from_offer": _order_differs,
    "status_order_mismatch": _status_mismatch,
}


def test_every_quote_decision_kind_has_an_injection():
    assert set(FINAL_INJECTIONS) | {"recommendation_differs_from_rule", "model_call_on_rerun"} == set(qd.CRITICAL_KINDS)


@pytest.mark.parametrize("kind", FINAL_INJECTIONS)
def test_each_injected_final_state_error_fails_quote_decision_with_exit_one(
    kind, tmp_path, capsys, monkeypatch, no_network
):
    item, folder = _one(tmp_path)
    real = qd._measure

    def measure(paths):
        final = copy.deepcopy(real(paths))
        FINAL_INJECTIONS[kind](final)
        return final

    monkeypatch.setattr(qd, "_measure", measure)
    assert qd.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert f"{kind}=1" in out
    assert f"  {item['id']}  critical errors" in out
    assert "GATE FAILED: critical_errors" in out


def test_a_recommendation_that_differs_from_the_rule_fails_with_exit_one(tmp_path, capsys, monkeypatch, no_network):
    item, folder = _one(tmp_path)
    real = qd._run_step

    def step(s, paths):
        code, out, err = real(s, paths)
        return code, out.replace("recommended: SUP-001", "recommended: SUP-002"), err

    monkeypatch.setattr(qd, "_run_step", step)
    assert qd.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert "recommendation_differs_from_rule=1" in out and "GATE FAILED: critical_errors" in out


def test_a_model_call_on_a_rerun_fails_with_exit_one(tmp_path, capsys, monkeypatch, no_network):
    item, folder = _one(tmp_path)
    real = qd._run_step

    def step(s, paths):
        code, out, err = real(s, paths)
        if s["op"] == "approve":
            err = f"Error: {qd.NO_RECORDING} the offer"
        return code, out, err

    monkeypatch.setattr(qd, "_run_step", step)
    assert qd.evaluate("replay", "test", dataset_dir=folder) == 1
    assert "model_call_on_rerun=1" in capsys.readouterr().out


def test_a_wrong_expectation_fails_quote_decision_with_exit_one(tmp_path, capsys, no_network):
    item, folder = _one(tmp_path)
    item["expected"]["status"] = "rejected"
    qd.write_items([item], folder)
    assert qd.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: scenario_success 0.0% below threshold 100.0%" in out
    assert f"  {item['id']}  expected" in out


def test_quote_decision_is_a_local_suite():
    assert "quote_decision" in harness.SUITES and "quote_decision" in harness.ALL_SUITES
    assert "quote_decision" not in harness.UPLOAD_SUITES
    assert harness._suite("quote_decision") is qd


def test_the_quote_decision_suite_passes_through_the_cli(capsys, no_network):
    assert cli.main(["eval", "--suite", "quote_decision"]) == 0
    out = capsys.readouterr().out
    assert f"quote_decision  mode=replay  scenarios={len(qd.load_dataset())}" in out
    for tag in qd.TAGS:
        assert f"  {tag:<18} 100.0%" in out
    assert no_network == []


def test_a_printed_reason_with_the_rule_but_wrong_totals_fails_the_scenario(tmp_path, capsys, monkeypatch, no_network):
    item, folder = _one(tmp_path)
    real = qd._run_step

    def step(s, paths):
        code, out, err = real(s, paths)
        return code, out.replace("EUR 49.20", "EUR 49.21"), err

    monkeypatch.setattr(qd, "_run_step", step)
    assert qd.evaluate("replay", "test", dataset_dir=folder) == 1
    assert f"  {item['id']}  expected" in capsys.readouterr().out


def test_the_oracle_counts_lead_times_from_the_decision_date():
    item = next(i for i in qd.load_dataset() if i["id"] == "QD-038")
    assert qd._decision_date(item) == qd.START + qd.timedelta(days=4)
    offers = qd._oracle_offers(item)
    assert (offers["SUP-001"]["lead"], offers["SUP-002"]["lead"]) == (5, 4)
    assert qd.oracle_winner(item) == "SUP-002"


def test_a_crash_after_the_offers_were_stored_is_a_scenario_that_would_catch_a_model_call_on_the_rerun():
    item = next(i for i in qd.load_dataset() if i["id"] == "QD-039")
    assert [s.get("crash") for s in item["steps"]] == ["extract", None, None]
    assert [s["exit"] for s in item["steps"]] == [1, 0, 0]

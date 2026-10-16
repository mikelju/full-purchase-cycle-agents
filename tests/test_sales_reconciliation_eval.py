"""C13 (phase 11): the deterministic `sales_reconciliation` evaluation, its scenarios, grader, report and gate.

One scenario is really run once (module fixture); the injections then replace the scripted steps and the
measurement by that run's rows, so each test stays fast and only the grader and the report are under test.
"""

import copy
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

import pytest

from purchase_cycle import cli, llm
from purchase_cycle.evaluation import harness
from purchase_cycle.evaluation import sales_reconciliation_eval as sr

ITEM_ID = "SR-018"  # an invoice with a missing, a damaged and an extra line: a credit note and a corrective invoice


def test_the_scenarios_meet_the_coverage_minimums():
    items = sr.load_dataset()
    tags = Counter(tag for item in items for tag in item["tags"])
    assert len(items) >= sr.MINIMUMS["scenarios"] == 30
    assert len({i["id"] for i in items}) == len(items)
    for tag in sr.TAGS:
        assert tags[tag] >= sr.MINIMUMS[tag] == 2, tag


def test_the_scenario_files_are_the_versioned_build():
    assert sr.load_dataset() == json.loads(
        json.dumps(sr.build_items())
    )  # the files hold lists where the build has tuples
    for item in sr.load_dataset():
        assert item["tags"] == sr.derive_tags(item), item["id"]


@pytest.fixture(scope="module")
def baseline():
    """The real run of one scenario: its item, its measured rows and its graded result."""
    item = next(i for i in sr.load_dataset() if i["id"] == ITEM_ID)
    measured = {}
    real = sr._measure

    def measure(*args):
        measured["final"] = real(*args)
        return measured["final"]

    restore = sr._count_calls()
    sr._measure = measure
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            result = sr.run_item(item, Path(tmp))
    finally:
        sr._measure = real
        restore()
    return item, measured["final"], result


def _stubbed(monkeypatch, baseline, mutate=None):
    """Skip the scripted steps and measure the baseline rows, mutated when asked (calls come from the counter)."""
    _, final, _ = baseline
    monkeypatch.setattr(sr, "_run_steps", lambda *a: None)

    def measure(*args):
        rows = copy.deepcopy(final)
        rows["calls"] = sum(sr.ACTIVE_CALLS)
        if mutate:
            mutate(rows)
        return rows

    monkeypatch.setattr(sr, "_measure", measure)


def _folder(tmp_path, item):
    sr.write_items([item], tmp_path / "cases")
    return tmp_path / "cases"


def test_the_real_scenario_is_correct_with_no_critical_error(baseline):
    _, _, result = baseline
    assert result["correct"] is True
    assert sum(result["critical"].values()) == 0
    assert set(result["critical"]) == set(sr.CRITICAL_KINDS)


def test_a_clean_scenario_passes_and_reports_the_intervals(tmp_path, capsys, monkeypatch, baseline):
    _stubbed(monkeypatch, baseline)
    assert sr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0])) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("scenario_success "))
    assert row.split()[1] == "100.0%" and "PASS" in row and "[" in row
    row = next(line for line in out.splitlines() if line.startswith("critical_errors "))
    assert row.split()[1] == "0" and row.endswith("PASS")
    assert "by kind: " + "  ".join(f"{kind}=0" for kind in sr.CRITICAL_KINDS) in out


# One injection per critical error: each mutates what the grader reads, not the module under test.


def _invoice_amount(final):
    final["invoices"][0]["net_total"] += 1


def _duplicate(final):
    final["invoices"].append(copy.deepcopy(final["invoices"][0]))


def _difference_amount(final):
    for rows in (final["recs"]["SINV-0001"][0]["diffs"], final["shown"]["SINV-0001"]["diffs"]):
        rows[0][2] += 1


def _missed(final):
    final["recs"]["SINV-0001"][0]["diffs"].pop(0)


def _invented(final):
    final["recs"]["SINV-0001"][0]["diffs"].append(["GLV-VIN-S", "damaged", 4.1])


def _correction_total(final):
    final["recs"]["SINV-0001"][0]["docs"][0][1] += 1


def _note_linked(final):
    final["notes"]["n1"]["note"] = 99


def _model_call(final):
    final["calls"] += 1


INJECTIONS = {
    "invoice_amount_wrong": _invoice_amount,
    "duplicate_invoice": _duplicate,
    "difference_amount_wrong": _difference_amount,
    "incident_missed": _missed,
    "incident_invented": _invented,
    "correction_total_wrong": _correction_total,
    "note_linked_wrong": _note_linked,
    "model_call": _model_call,
}


def test_every_critical_kind_has_an_injection():
    assert set(INJECTIONS) == set(sr.CRITICAL_KINDS)


@pytest.mark.parametrize("kind", INJECTIONS)
def test_each_injected_critical_error_fails_the_suite_with_exit_one(kind, tmp_path, capsys, monkeypatch, baseline):
    _stubbed(monkeypatch, baseline, INJECTIONS[kind])
    assert sr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0])) == 1
    out = capsys.readouterr().out
    assert re.search(rf"{kind}=[1-9]", out)
    assert f"  {ITEM_ID}  critical errors" in out
    assert "GATE FAILED: critical_errors" in out


def test_a_model_client_call_is_counted_and_raises(tmp_path, capsys, monkeypatch, baseline):
    def steps(*args):
        for call in (lambda: llm.ModelClient(), lambda: llm.ModelClient._call_model(None)):
            with pytest.raises(llm.MissingRecording):
                call()

    _stubbed(monkeypatch, baseline)
    monkeypatch.setattr(sr, "_run_steps", steps)
    assert sr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0])) == 1
    assert "model_call=2" in capsys.readouterr().out
    assert sr.ACTIVE_CALLS == [1, 1] and llm.ModelClient.__init__ is not sr._count_calls  # restored below


def test_the_model_client_is_restored_after_a_run(tmp_path, monkeypatch, baseline):
    before = (llm.ModelClient.__init__, llm.ModelClient._call_model)
    _stubbed(monkeypatch, baseline)
    sr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0]))
    assert (llm.ModelClient.__init__, llm.ModelClient._call_model) == before


def test_a_wrong_expectation_fails_the_suite_with_exit_one(tmp_path, capsys, monkeypatch, baseline):
    item = copy.deepcopy(baseline[0])
    item["expected"]["results"]["SINV-0001"]["result"] = "matched"
    _stubbed(monkeypatch, baseline)
    assert sr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, item)) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: scenario_success 0.0% below threshold 100.0%" in out
    assert f"  {ITEM_ID}  expected" in out


def test_the_grader_counts_missed_invented_and_swapped_rows():
    assert sr.compare_rows([["A", "missing", 1.0]], [["A", "missing", 1.0]]) == (0, 0)
    assert sr.compare_rows([["A", "missing", 1.0]], []) == (1, 0)
    assert sr.compare_rows([], [["A", "missing", 1.0]]) == (0, 1)


def test_sales_reconciliation_is_a_local_suite():
    assert "sales_reconciliation" in harness.SUITES and harness.ALL_SUITES[-2] == "sales_reconciliation"
    assert "sales_reconciliation" not in harness.UPLOAD_SUITES
    assert harness._suite("sales_reconciliation") is sr


def test_the_suite_passes_through_the_cli(capsys, monkeypatch, baseline):
    item, _, _ = baseline
    monkeypatch.setattr(sr, "load_dataset", lambda *a, **k: [item])
    _stubbed(monkeypatch, baseline)
    assert cli.main(["eval", "--suite", "sales_reconciliation"]) == 0
    out = capsys.readouterr().out
    assert "sales_reconciliation  mode=replay  scenarios=1" in out
    assert "  invoice_per_note             100.0%" in out

"""C13 (phase 11): the deterministic `customer_response` evaluation, its scenarios, grader, report and gate.

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
from purchase_cycle.evaluation import customer_response_eval as cr
from purchase_cycle.evaluation import harness

ITEM_ID = "CR-001"  # a discrepancies invoice: the response is drafted, then approved as drafted and sent


def test_the_scenarios_meet_the_coverage_minimums():
    items = cr.load_dataset()
    tags = Counter(tag for item in items for tag in item["tags"])
    assert len(items) >= cr.MINIMUMS["scenarios"] == 24
    assert len({i["id"] for i in items}) == len(items)
    for tag in cr.TAGS:
        assert tags[tag] >= cr.MINIMUMS[tag] == 2, tag


def test_the_scenario_files_are_the_versioned_build():
    assert cr.load_dataset() == json.loads(json.dumps(cr.build_items()))  # the files hold lists, the build tuples


@pytest.fixture(scope="module")
def baseline():
    """The real run of one scenario: its item, its final rows and its graded result."""
    item = next(i for i in cr.load_dataset() if i["id"] == ITEM_ID)
    captured = {}
    real = cr._errors

    def errors(it, final):
        captured["final"] = final
        return real(it, final)

    restore = cr._count_calls()
    cr._errors = errors
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            result = cr.run_item(item, Path(tmp))
    finally:
        cr._errors = real
        restore()
    return item, captured["final"], result


def _stubbed(monkeypatch, baseline, mutate=None, extra_calls=0):
    """Skip the setup and the scripted steps; measure the baseline rows, mutated when asked."""
    _, final, _ = baseline
    steps = iter(range(len(final["codes"])))

    def run_step(item, op, paths, double, folder):
        n = next(steps)
        double.calls = final["calls"] + extra_calls
        out = final["outputs"][n]
        return (
            final["codes"][n],
            ("" if out in ("", "error") else f"response {out}"),
            "Error: stub" if out == "error" else "",
        )

    def measure(paths):
        rows = copy.deepcopy(final)
        if mutate:
            mutate(rows)
        return rows

    monkeypatch.setattr(cr, "_setup", lambda *a: None)
    monkeypatch.setattr(cr, "_run_step", run_step)
    monkeypatch.setattr(cr, "_measure", measure)


def _folder(tmp_path, item):
    cr.write_items([item], tmp_path / "cases")
    return tmp_path / "cases"


def test_the_real_scenario_is_correct_with_no_critical_error(baseline):
    _, _, result = baseline
    assert result["correct"] is True
    assert sum(result["critical"].values()) == 0
    assert set(result["critical"]) == set(cr.CRITICAL_KINDS)


def test_a_clean_scenario_passes_and_reports_the_intervals(tmp_path, capsys, monkeypatch, baseline):
    _stubbed(monkeypatch, baseline)
    assert cr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0])) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("scenario_success "))
    assert row.split()[1] == "100.0%" and "PASS" in row and "[" in row
    row = next(line for line in out.splitlines() if line.startswith("critical_errors "))
    assert row.split()[1] == "0" and row.endswith("PASS")
    assert "by kind: " + "  ".join(f"{kind}=0" for kind in cr.CRITICAL_KINDS) in out


# One injection per critical error: each mutates what the grader reads, not the module under test.


def _unapproved(final):
    final["responses"]["SINV-0001"]["status"] = "drafted"


def _duplicate(final):
    final["emails"]["RESPONSE-SINV-0001-2.eml"] = copy.deepcopy(final["emails"]["RESPONSE-SINV-0001.eml"])


def _text_differs(final):
    final["emails"]["RESPONSE-SINV-0001.eml"]["body"] += " changed"
    final["responses"]["SINV-0001"]["final_body"] += " changed"


def _amount(final):
    final["docs"]["SINV-0001"][0][2] += 1


def _outbox(final):
    final["responses"]["SINV-0001"]["outbox_file"] = None


INJECTIONS = {
    "unapproved_send": (_unapproved, 0),
    "duplicate_email": (_duplicate, 0),
    "sent_text_differs": (_text_differs, 0),
    "document_amount_changed": (_amount, 0),
    "status_outbox_mismatch": (_outbox, 0),
    "model_call": (None, 1),
}


def test_every_critical_kind_has_an_injection():
    assert set(INJECTIONS) == set(cr.CRITICAL_KINDS)


@pytest.mark.parametrize("kind", INJECTIONS)
def test_each_injected_critical_error_fails_the_suite_with_exit_one(kind, tmp_path, capsys, monkeypatch, baseline):
    mutate, extra = INJECTIONS[kind]
    _stubbed(monkeypatch, baseline, mutate, extra)
    assert cr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0])) == 1
    out = capsys.readouterr().out
    assert re.search(rf"{kind}=[1-9]", out)
    assert "GATE FAILED: critical_errors" in out


def test_a_model_client_call_is_counted_and_raises(tmp_path, capsys, monkeypatch, baseline):
    _stubbed(monkeypatch, baseline)
    run_step = cr._run_step

    def step(*args):
        for call in (lambda: llm.ModelClient(), lambda: llm.ModelClient._call_model(None)):
            with pytest.raises(llm.MissingRecording):
                call()
        return run_step(*args)

    monkeypatch.setattr(cr, "_run_step", step)
    assert cr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0])) == 1
    assert "model_call=4" in capsys.readouterr().out
    assert cr.ACTIVE_CALLS == [1] * 4


def test_the_model_client_is_restored_after_a_run(tmp_path, monkeypatch, baseline):
    before = (llm.ModelClient.__init__, llm.ModelClient._call_model)
    _stubbed(monkeypatch, baseline)
    cr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0]))
    assert (llm.ModelClient.__init__, llm.ModelClient._call_model) == before


def test_a_wrong_expectation_fails_the_suite_with_exit_one(tmp_path, capsys, monkeypatch, baseline):
    item = copy.deepcopy(baseline[0])
    item["expected"]["results"]["SINV-0001"] = "matched"
    _stubbed(monkeypatch, baseline)
    assert cr.evaluate("replay", "test", dataset_dir=_folder(tmp_path, item)) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: scenario_success 0.0% below threshold 100.0%" in out
    assert f"  {ITEM_ID}  expected" in out


def test_customer_response_is_a_local_suite():
    assert "customer_response" in harness.SUITES and harness.ALL_SUITES[-2] == "customer_response"
    assert "customer_response" not in harness.UPLOAD_SUITES
    assert harness._suite("customer_response") is cr


def test_the_suite_passes_through_the_cli(capsys, monkeypatch, baseline):
    item, _, _ = baseline
    monkeypatch.setattr(cr, "load_dataset", lambda *a, **k: [item])
    _stubbed(monkeypatch, baseline)
    assert cli.main(["eval", "--suite", "customer_response"]) == 0
    out = capsys.readouterr().out
    assert "customer_response  mode=replay  scenarios=1" in out
    assert "  discrepancies_response       100.0%" in out

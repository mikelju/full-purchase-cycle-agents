"""C19/C20 (phase 11): the `response_drafting` evaluation, its report and gates, on synthetic recordings."""

import argparse
import json

import pytest

from purchase_cycle import customer_responses
from purchase_cycle.evaluation import harness, response_eval
from purchase_cycle.evaluation import response_dataset as cd
from purchase_cycle.llm import RESPONSE_DRAFTING, build_system_prompt, recording_key

PER_CATEGORY = 3
FLIPPED = 6  # enough flipped responses for a significant McNemar drop (p = 2 * 0.5**6)


def _subset() -> list[dict]:
    test = [c for c in cd.load_dataset() if c["split"] == "test"]
    cases = []
    for category in cd.CATEGORIES:
        cases += [c for c in test if c["category"] == category][:PER_CATEGORY]
    return cases


def perfect(case: dict) -> dict:
    return {"body": customer_responses.fill_template(case["facts"])}


def _no_invoice(case: dict) -> dict:
    body = customer_responses.fill_template(case["facts"]).replace(case["facts"]["invoice"], "")
    return {"body": body}


@pytest.fixture
def suite(tmp_path):
    cases = _subset()
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    prompt = build_system_prompt([], RESPONSE_DRAFTING)
    recordings = tmp_path / "recordings.jsonl"

    def write(answers=None):
        answers = answers or {}
        rows = []
        for c in cases:
            text = cd.model_text(c)
            key = recording_key(prompt, text, RESPONSE_DRAFTING)
            rows.append({"key": key, "case_id": c["id"], "sentence": text, "answer": answers.get(c["id"], perfect(c))})
        recordings.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def run(**kwargs):
        return response_eval.evaluate(
            "replay",
            "test",
            dataset_path=dataset,
            recordings_path=recordings,
            baseline_path=tmp_path / "baseline.json",
            workers=2,
            **kwargs,
        )

    run.cases, run.write, run.baseline = cases, write, tmp_path / "baseline.json"
    return run


def _set_baseline(suite):
    suite.write()
    assert suite(set_baseline=True) == 0


def _row(out, metric):
    return next(line for line in out.splitlines() if line.startswith(metric + " "))


def test_perfect_drafts_pass_and_report_every_metric_globally_and_per_category(suite, capsys):
    _set_baseline(suite)
    capsys.readouterr()
    suite.write()
    assert suite() == 0
    out = capsys.readouterr().out
    for metric in response_eval.METRICS:
        assert _row(out, metric)
    assert "100.0%" in _row(out, "case_pass_rate")
    assert "[" in _row(out, "incident_coverage")  # Wilson interval
    assert "0 of" in _row(out, "critical_errors") and "Wilson upper bound" in _row(out, "critical_errors")
    assert "0 of" in _row(out, "invented_amount_rate")
    assert "no significant drop" in out
    for category in cd.CATEGORIES:
        assert category in out


def test_without_baseline_the_gates_fail(suite, capsys):
    suite.write()
    assert suite() == 1
    assert "no baseline stored" in capsys.readouterr().out


def test_absolute_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write({c["id"]: _no_invoice(c) for c in suite.cases[:3]})
    assert suite() == 1
    assert "GATE FAILED: case_pass_rate" in capsys.readouterr().out


def test_regression_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write({c["id"]: _no_invoice(c) for c in suite.cases[:FLIPPED]})
    assert suite() == 1
    assert "case_pass_rate dropped against the baseline" in capsys.readouterr().out


def test_missing_recordings_exit_two(suite, capsys):
    assert suite() == 2
    assert "No recording" in capsys.readouterr().err


def test_set_baseline_below_threshold_stops_with_exit_three_and_writes_nothing(suite, capsys):
    suite.write({c["id"]: _no_invoice(c) for c in suite.cases[:3]})
    assert suite(set_baseline=True) == 3
    assert "STOP" in capsys.readouterr().out
    assert not suite.baseline.exists()


def test_schema_failure_grades_as_draft_failed(suite, capsys):
    _set_baseline(suite)
    bad = suite.cases[0]
    suite.write({bad["id"]: {"body": 5}})
    suite()
    out = capsys.readouterr().out
    assert f"{bad['id']}  draft_failed" in out


def test_grader_scores_the_model_draft_without_the_template_fallback():
    case = cd.load_dataset()[0]
    r = response_eval.grade(case, "Sorry for the trouble.")
    assert r["body"] == "Sorry for the trouble." and not r["case_pass_rate"] and r["problems"]


def test_each_check_and_critical_error_is_graded():
    case = next(c for c in cd.load_dataset() if c["category"] == "both_documents")
    facts = case["facts"]
    text = customer_responses.fill_template(facts)
    ok = response_eval.grade(case, text)
    assert ok["case_pass_rate"] and not ok["critical_errors"]
    assert ok["incidents_covered"] == ok["incidents"] and ok["documents_ok"] == ok["documents"]
    no_invoice = response_eval.grade(case, text.replace(facts["invoice"], ""))
    assert not no_invoice["reference_accuracy"] and no_invoice["critical_errors"] and not no_invoice["case_pass_rate"]
    no_sku = response_eval.grade(case, text.replace(facts["incidents"][0]["sku"], "item"))
    assert no_sku["incidents_covered"] < no_sku["incidents"] and no_sku["critical_errors"]
    no_document = response_eval.grade(case, text.replace(facts["documents"][0]["number"], "the document"))
    assert no_document["documents_ok"] < no_document["documents"] and no_document["critical_errors"]
    invented = response_eval.grade(case, text + "\nWe also owe 987.65 EUR.")
    assert invented["invented_amount_rate"] and invented["critical_errors"]
    failed = response_eval.grade(case, None, "InvalidModelOutput: x")
    assert failed["critical_errors"] and not failed["case_pass_rate"] and failed["documents_ok"] == 0


def test_the_suite_is_registered_for_run_only_and_outside_all_suites(monkeypatch):
    assert harness.SUITES[-1] == "response_drafting"
    assert "response_drafting" not in harness.UPLOAD_SUITES and "response_drafting" not in harness.ALL_SUITES
    ran = []
    monkeypatch.setattr(
        harness, "_suite", lambda name: argparse.Namespace(evaluate=lambda *a, **k: ran.append(name) or 0)
    )
    args = argparse.Namespace(suite=["response_drafting"], mode="replay", split="test", set_baseline=False, workers=1)
    assert harness.cmd_eval(args) == 0
    assert ran == ["response_drafting"]
    monkeypatch.undo()
    assert harness._suite("response_drafting") is response_eval

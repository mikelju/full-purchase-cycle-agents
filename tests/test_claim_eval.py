"""C13 (phase 09): the `claim_drafting` evaluation, its report and gates, on synthetic recordings."""

import argparse
import json

import pytest

from purchase_cycle import claims
from purchase_cycle.evaluation import claim_dataset as cd
from purchase_cycle.evaluation import claim_eval, harness
from purchase_cycle.llm import CLAIM_DRAFTING, build_system_prompt, recording_key

PER_CATEGORY = 3
FLIPPED = 6  # enough flipped claims for a significant McNemar drop (p = 2 * 0.5**6)


def _subset() -> list[dict]:
    test = [c for c in cd.load_dataset() if c["split"] == "test"]
    cases = []
    for category in cd.CATEGORIES:
        cases += [c for c in test if c["category"] == category][:PER_CATEGORY]
    return cases


def perfect(case: dict) -> dict:
    return {"body": claims.fill_template(case["facts"])}


def _without(case: dict, text: str) -> dict:
    return {"body": claims.fill_template(case["facts"]).replace(text, "")}


@pytest.fixture
def suite(tmp_path):
    cases = _subset()
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    prompt = build_system_prompt([], CLAIM_DRAFTING)
    recordings = tmp_path / "recordings.jsonl"

    def write(answers=None):
        answers = answers or {}
        rows = []
        for c in cases:
            text = cd.model_text(c)
            key = recording_key(prompt, text, CLAIM_DRAFTING)
            rows.append({"key": key, "case_id": c["id"], "sentence": text, "answer": answers.get(c["id"], perfect(c))})
        recordings.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def run(**kwargs):
        return claim_eval.evaluate(
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


def _no_action(case):
    return {"body": claims.fill_template(case["facts"]).replace("corrected invoice or a credit note", "reply")}


def test_perfect_drafts_pass_and_report_every_metric_globally_and_per_category(suite, capsys):
    _set_baseline(suite)
    capsys.readouterr()
    suite.write()
    assert suite() == 0
    out = capsys.readouterr().out
    for metric in claim_eval.METRICS:
        assert _row(out, metric)
    assert "100.0%" in _row(out, "case_pass_rate")
    assert "[" in _row(out, "discrepancy_coverage")  # Wilson interval
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
    suite.write({c["id"]: _no_action(c) for c in suite.cases[:3]})
    assert suite() == 1
    assert "GATE FAILED: case_pass_rate" in capsys.readouterr().out


def test_regression_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write({c["id"]: _no_action(c) for c in suite.cases[:FLIPPED]})
    assert suite() == 1
    assert "case_pass_rate dropped against the baseline" in capsys.readouterr().out


def test_missing_recordings_exit_two(suite, capsys):
    assert suite() == 2
    assert "No recording" in capsys.readouterr().err


def test_set_baseline_below_threshold_stops_with_exit_three_and_writes_nothing(suite, capsys):
    suite.write({c["id"]: _no_action(c) for c in suite.cases[:3]})
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
    r = claim_eval.grade(case, "Please pay less.")
    assert r["body"] == "Please pay less." and not r["case_pass_rate"] and r["problems"]


def test_each_check_and_critical_error_is_graded():
    case = next(c for c in cd.load_dataset() if c["category"] == "price_above")
    facts = case["facts"]
    ok = claim_eval.grade(case, claims.fill_template(facts))
    assert ok["case_pass_rate"] and not ok["critical_errors"] and ok["rows_covered"] == ok["rows"]
    row = facts["differences"][0]
    no_ref = claim_eval.grade(case, claims.fill_template(facts).replace(facts["invoice_number"], "X"))
    assert not no_ref["reference_accuracy"] and no_ref["critical_errors"]
    no_total = claim_eval.grade(case, claims.fill_template(facts).replace("amount in dispute is", "total is"))
    assert no_total["amount_in_dispute_accuracy"]  # the wording is free; the amount is what counts
    several = next(c for c in cd.load_dataset() if c["category"] == "several_types")
    text = claims.fill_template(several["facts"])
    wrong_total = claim_eval.grade(
        several, text.replace(f"{several['facts']['amount_in_dispute']:.2f} EUR.", "1.11 EUR.")
    )
    assert not wrong_total["amount_in_dispute_accuracy"] and wrong_total["invented_amount_rate"]
    assert wrong_total["critical_errors"]
    no_row = claim_eval.grade(case, claims.fill_template(facts).replace(row["sku"], "item"))
    assert no_row["rows_covered"] < no_row["rows"] and no_row["critical_errors"]
    no_action = claim_eval.grade(case, _no_action(case)["body"])
    assert not no_action["action_accuracy"] and not no_action["case_pass_rate"] and not no_action["critical_errors"]
    invented = claim_eval.grade(case, claims.fill_template(facts) + "\nWe also owe 987.65 EUR.")
    assert invented["invented_amount_rate"] and invented["critical_errors"]
    failed = claim_eval.grade(case, None, "InvalidModelOutput: x")
    assert failed["critical_errors"] and not failed["case_pass_rate"] and failed["rows_covered"] == 0


def test_amounts_are_read_with_a_decimal_comma_and_half_a_cent():
    case = next(c for c in cd.load_dataset() if c["category"] == "decimals_thousands")
    body = claims.fill_template(case["facts"])
    total = f"{case['facts']['amount_in_dispute']:.2f}"
    assert claim_eval.grade(case, body.replace(total, total.replace(".", ",")))["amount_in_dispute_accuracy"]


def test_the_suite_is_registered_for_run_and_upload_but_not_in_all(monkeypatch):
    assert "claim_drafting" in harness.SUITES and "claim_drafting" in harness.UPLOAD_SUITES
    assert "claim_drafting" not in harness.ALL_SUITES
    ran = []
    monkeypatch.setattr(
        harness, "_suite", lambda name: argparse.Namespace(evaluate=lambda *a, **k: ran.append(name) or 0)
    )
    args = argparse.Namespace(suite=["claim_drafting"], mode="replay", split="test", set_baseline=False, workers=1)
    assert harness.cmd_eval(args) == 0
    assert ran == ["claim_drafting"]
    monkeypatch.undo()
    assert harness._suite("claim_drafting") is claim_eval

"""C12 (phase 07): the `offer_extraction` evaluation, its report and gates, on hand-written recordings."""

import argparse
import json

import pytest

from purchase_cycle.evaluation import harness, offer_eval
from purchase_cycle.evaluation import offer_dataset as od
from purchase_cycle.llm import OFFER_EXTRACTION, build_system_prompt, recording_key

PER_CATEGORY = 3
FLIPPED = 6  # enough flipped replies for a significant McNemar drop (p = 2 * 0.5**6)


def _subset() -> list[dict]:
    test = [c for c in od.load_dataset() if c["split"] == "test"]
    cases = []
    for category in od.CATEGORIES:
        cases += [c for c in test if c["category"] == category][:PER_CATEGORY]
    return cases


def perfect(case: dict) -> dict:
    return {**dict.fromkeys(od.GRADED), **case["expected"], "price_text": None}


@pytest.fixture
def suite(tmp_path):
    cases = _subset()
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    prompt = build_system_prompt([], OFFER_EXTRACTION)
    recordings = tmp_path / "recordings.jsonl"

    def write(answers=None):
        answers = answers or {}
        rows = []
        for c in cases:
            text = od.model_text(c)
            answer = answers.get(c["id"], perfect(c))
            key = recording_key(prompt, text, OFFER_EXTRACTION)
            rows.append({"key": key, "case_id": c["id"], "sentence": text, "answer": answer})
        recordings.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def run(**kwargs):
        return offer_eval.evaluate(
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


def _priced(suite):
    return [c for c in suite.cases if c["expected"].get("unit_price") is not None]


def _price_off(case):
    return {**perfect(case), "unit_price": case["expected"]["unit_price"] + 1}


def test_perfect_answers_pass_and_report_every_metric_globally_and_per_category(suite, capsys):
    _set_baseline(suite)
    capsys.readouterr()
    suite.write()
    assert suite() == 0
    out = capsys.readouterr().out
    for metric in offer_eval.METRICS:
        assert _row(out, metric)
    assert "100.0%" in _row(out, "exact_match")
    assert "[" in _row(out, "unit_price_accuracy")  # Wilson interval
    assert "0 of" in _row(out, "critical_errors") and "Wilson upper bound" in _row(out, "critical_errors")
    assert "no significant drop" in out
    for category in od.CATEGORIES:
        assert category in out


def test_without_baseline_the_gates_fail(suite, capsys):
    suite.write()
    assert suite() == 1
    assert "no baseline stored" in capsys.readouterr().out


def test_exact_match_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    victims = [c for c in suite.cases if c["expected"].get("currency") is None][:4] or suite.cases[:4]
    suite.write({c["id"]: {**perfect(c), "price_text": "x", "lead_time_days": 99} for c in victims})
    assert suite() == 1
    assert "GATE FAILED: exact_match" in capsys.readouterr().out


def test_unit_price_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write({c["id"]: _price_off(c) for c in _priced(suite)[:3]})
    assert suite() == 1
    assert "GATE FAILED: unit_price_accuracy" in capsys.readouterr().out


def test_exact_match_regression_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    flipped = {c["id"]: {**perfect(c), "lead_time_days": 99} for c in suite.cases[:FLIPPED]}
    suite.write(flipped)
    assert suite() == 1
    out = capsys.readouterr().out
    assert "exact_match dropped against the baseline" in out


def test_unit_price_regression_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write({c["id"]: _price_off(c) for c in _priced(suite)[:FLIPPED]})
    assert suite() == 1
    assert "unit_price_accuracy dropped against the baseline" in capsys.readouterr().out


def test_missing_recordings_exit_two(suite, capsys):
    assert suite() == 2
    assert "No recording" in capsys.readouterr().err


def test_set_baseline_below_threshold_stops_with_exit_three_and_writes_nothing(suite, capsys):
    suite.write({c["id"]: _price_off(c) for c in _priced(suite)[:3]})
    assert suite(set_baseline=True) == 3
    assert "STOP" in capsys.readouterr().out
    assert not suite.baseline.exists()


def test_schema_failure_grades_as_extraction_failed(suite, capsys):
    _set_baseline(suite)
    bad = suite.cases[0]
    suite.write({bad["id"]: {"offers": "yes"}})
    suite()
    out = capsys.readouterr().out
    assert f"{bad['id']}  extraction_failed" in out


def test_critical_error_is_a_usable_reply_with_a_wrong_price_quantity_or_currency():
    usable = next(c for c in od.load_dataset() if c["expected_usable"])
    assert not offer_eval.grade(usable, perfect(usable))["critical_errors"]
    wrong = offer_eval.grade(usable, {**perfect(usable), "unit_price": usable["expected"]["unit_price"] + 1})
    assert wrong["usable"] and wrong["critical_errors"]
    declined = next(c for c in od.load_dataset() if c["expected_reason"] == "declined")
    refused = offer_eval.grade(declined, {**perfect(declined), "offers": False, "quantity": None})
    assert not refused["usable"] and not refused["critical_errors"]


def test_graders_apply_the_half_cent_tolerance_and_the_applicable_fields():
    priced = next(c for c in od.load_dataset() if c["expected"].get("unit_price") is not None)
    near = offer_eval.grade(priced, {**perfect(priced), "unit_price": priced["expected"]["unit_price"] + 0.004})
    assert near["unit_price_accuracy"] is True
    far = offer_eval.grade(priced, {**perfect(priced), "unit_price": priced["expected"]["unit_price"] + 0.01})
    assert far["unit_price_accuracy"] is False and far["exact_match"] is False
    declined = next(c for c in od.load_dataset() if c["expected_reason"] == "declined")
    r = offer_eval.grade(declined, perfect(declined))
    assert r["quantity_accuracy"] is None and r["unit_price_accuracy"] is None and r["offer_accuracy"] is True


def test_eval_all_includes_the_suite_once_its_baseline_is_recorded(monkeypatch):
    ran = []
    monkeypatch.setattr(
        harness, "_suite", lambda name: argparse.Namespace(evaluate=lambda *a, **k: ran.append(name) or 0)
    )
    args = argparse.Namespace(suite="all", mode="replay", split="test", set_baseline=False, workers=1)
    assert harness.cmd_eval(args) == 0
    assert ran[-7:] == [
        "offer_extraction",
        "quote_decision",
        "reconciliation_matching",
        "document_extraction",
        "claim_approval",
        "claim_drafting",
        "sales_reconciliation",
    ]
    ran.clear()
    args.suite = ["offer_extraction"]
    assert harness.cmd_eval(args) == 0
    assert ran == ["offer_extraction"]


def test_eval_upload_includes_the_offer_suite(monkeypatch):
    uploaded = []
    monkeypatch.setattr(
        harness, "_suite", lambda name: argparse.Namespace(upload_datasets=lambda: uploaded.append(name) or 0)
    )
    assert harness.cmd_upload(argparse.Namespace(suite="all")) == 0
    assert "offer_extraction" in uploaded


def test_critical_error_also_fires_when_the_vat_misread_moves_the_net_price():
    case = next(c for c in od.load_dataset() if c["id"] == "OFF-074")
    assert case["expected"]["vat_included"] and case["expected_net_price"] is not None
    assert not offer_eval.grade(case, perfect(case))["critical_errors"]
    misread = offer_eval.grade(case, {**perfect(case), "vat_included": False, "vat_rate": None})
    assert misread["usable"] and misread["unit_price_accuracy"] is True and misread["critical_errors"]
    near = {**perfect(case), "unit_price": case["expected"]["unit_price"] + 0.004}
    assert not offer_eval.grade(case, near)["critical_errors"]


def test_per_category_report_lists_offer_vat_and_lead_time_accuracy(suite, capsys):
    _set_baseline(suite)
    capsys.readouterr()
    suite()
    out = capsys.readouterr().out.split("per category:")[1]
    for metric in ("offer_accuracy", "vat_accuracy", "lead_time_accuracy"):
        assert metric in out


@pytest.mark.parametrize(("case_id", "invented"), [("OFF-082", {"vat_rate": 21.0}), ("OFF-095", {"lead_time_days": 2})])
def test_a_reply_that_must_be_unusable_but_the_model_makes_usable_is_a_critical_error(case_id, invented):
    case = next(c for c in od.load_dataset() if c["id"] == case_id)
    assert not case["expected_usable"]
    refused = offer_eval.grade(case, perfect(case))
    assert not refused["usable"] and not refused["critical_errors"]
    made_usable = offer_eval.grade(case, {**perfect(case), **invented})
    assert made_usable["usable"] and made_usable["critical_errors"]

"""C3 and C11: web form matching evaluation report and gates."""

import argparse
import json

import pytest

from purchase_cycle.config import MATCHING_RECORDINGS_PATH
from purchase_cycle.evaluation import harness, web_form_eval
from purchase_cycle.evaluation import web_form_dataset as wf


@pytest.fixture
def baseline_copy(tmp_path):
    def _copy(**changes):
        data = json.loads(web_form_eval.BASELINE_PATH.read_text(encoding="utf-8"))
        data.update(changes)
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return _copy


def test_replay_evaluation_passes_and_reports(capsys):
    assert web_form_eval.evaluate("replay", "test") == 0
    out = capsys.readouterr().out
    assert "web_form_matching  mode=replay  split=test  lines=468" in out
    for text in ("line_product_accuracy", "submission_accuracy", "95% CI", "per category:", "regression vs baseline"):
        assert text in out
    # C3: the deterministic categories never reach the model.
    for category in wf.SCRIPT_CATEGORIES:
        assert "model calls 0" in next(line for line in out.splitlines() if line.strip().startswith(category))


def test_absolute_gate_failure_exits_non_zero(baseline_copy, capsys):
    path = baseline_copy(threshold=0.9999)
    assert web_form_eval.evaluate("replay", "test", baseline_path=path) == 1
    assert "GATE FAILED: line product_accuracy" in capsys.readouterr().out


def test_model_calls_in_deterministic_categories_fail_the_gate():
    summary = {"product_accuracy": {"value": 1.0}}
    failures = web_form_eval.absolute_gates(summary, {"exact_name": 0, "sku_typed": 2, "typo": 30}, 0.95)
    assert failures == ["sku_typed made 2 model calls; deterministic categories must make none"]


def test_regression_gate_failure_exits_non_zero(baseline_copy, tmp_path, capsys):
    # Worse recordings: 40 model answers of the test split now name a wrong SKU.
    texts = {line["product_text"] for line in wf.load_dataset() if line["split"] == "test"}
    rows = [json.loads(line) for line in MATCHING_RECORDINGS_PATH.read_text(encoding="utf-8").splitlines() if line]
    changed = 0
    for row in rows:
        if row["sentence"] in texts and changed < 40:
            row["answer"] = {"sku": "PILL-CRUSH"}
            changed += 1
    worse = tmp_path / "worse.jsonl"
    worse.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    path = baseline_copy(threshold=0.0)
    assert web_form_eval.evaluate("replay", "test", recordings_path=worse, baseline_path=path) == 1
    assert "GATE FAILED: product_accuracy dropped against the baseline" in capsys.readouterr().out


def test_missing_recordings_exit_with_error(tmp_path, capsys):
    assert web_form_eval.evaluate("replay", "test", recordings_path=tmp_path / "none.jsonl") == 2
    assert "--mode record" in capsys.readouterr().err


def test_eval_command_runs_both_suites_and_keeps_the_worst_exit(monkeypatch, capsys):
    monkeypatch.setattr(harness, "evaluate", lambda *args, **kwargs: 0)
    monkeypatch.setattr(web_form_eval, "evaluate", lambda *args, **kwargs: 1)
    args = argparse.Namespace(suite="all", mode="replay", split="test", set_baseline=False, workers=1)
    assert harness.cmd_eval(args) == 1
    args.suite = "order_line_extraction"
    assert harness.cmd_eval(args) == 0


def test_set_baseline_needs_one_suite(capsys):
    args = argparse.Namespace(suite="all", mode="record", split="test", set_baseline=True, workers=1)
    assert harness.cmd_eval(args) == 2
    assert "one --suite" in capsys.readouterr().err


def test_submission_accuracy_needs_every_line_right():
    results = [
        {"submission_id": "S1", "product_accuracy": True},
        {"submission_id": "S1", "product_accuracy": False},
        {"submission_id": "S2", "product_accuracy": True},
    ]
    summary = web_form_eval.summarise(results)
    assert (summary["submission_accuracy"]["hits"], summary["submission_accuracy"]["n"]) == (1, 2)
    assert (summary["product_accuracy"]["hits"], summary["product_accuracy"]["n"]) == (2, 3)

"""C11: replay evaluation report and both gates."""

import json
import shutil

import pytest

from purchase_cycle.config import RECORDINGS_PATH
from purchase_cycle.evaluation import harness
from purchase_cycle.evaluation.dataset import load_dataset


@pytest.fixture
def baseline_copy(tmp_path):
    def _copy(**changes):
        data = json.loads(harness.BASELINE_PATH.read_text(encoding="utf-8"))
        data.update(changes)
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return _copy


def test_replay_evaluation_passes_and_reports(capsys):
    assert harness.evaluate("replay", "test") == 0
    out = capsys.readouterr().out
    assert "mode=replay  split=test  cases=1500" in out
    for text in (
        "product_accuracy",
        "quantity_accuracy",
        "95% CI",
        "per category:",
        "contrast set (hand-curated, n=60)",
    ):
        assert text in out
    assert "regression vs baseline" in out


def test_absolute_gate_failure_exits_non_zero(baseline_copy, capsys):
    path = baseline_copy(threshold=0.9999)
    assert harness.evaluate("replay", "test", baseline_path=path) == 1
    assert "GATE FAILED: product_accuracy" in capsys.readouterr().out


def test_regression_gate_failure_exits_non_zero(baseline_copy, tmp_path, capsys):
    # Worse recordings: 40 test cases now answer a wrong SKU.
    targets = {c["id"] for c in load_dataset() if c["split"] == "test"}
    rows = [json.loads(line) for line in RECORDINGS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = 0
    for row in rows:
        if row["case_id"] in targets and changed < 40:
            row["answer"] = {"sku": "PILL-CRUSH", "quantity": row["answer"].get("quantity", 1)}
            changed += 1
    worse = tmp_path / "worse.jsonl"
    worse.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    path = baseline_copy(threshold=0.0)
    assert harness.evaluate("replay", "test", recordings_path=worse, baseline_path=path) == 1
    assert "GATE FAILED: product_accuracy dropped against the baseline" in capsys.readouterr().out


def test_missing_recordings_exit_with_error(tmp_path, capsys):
    empty = tmp_path / "none.jsonl"
    assert harness.evaluate("replay", "test", recordings_path=empty) == 2
    assert "--mode record" in capsys.readouterr().err


def test_regression_gate_ignores_noise():
    results = [{"id": str(i), "product_accuracy": i != 0, "quantity_accuracy": True} for i in range(100)]
    baseline = {"cases": {str(i): {"product_accuracy": i != 1, "quantity_accuracy": True} for i in range(100)}}
    failures, tests = harness.regression_gate(results, baseline)
    assert failures == []
    assert tests["product_accuracy"] == {"lost": 1, "gained": 1, "p": 1.0}


def test_eval_is_wired_into_check():
    package = json.loads(open("package.json", encoding="utf-8").read())
    assert "npm run --silent eval" in package["scripts"]["check"]
    assert "--mode replay" in package["scripts"]["eval"]
    assert shutil.which("uv")

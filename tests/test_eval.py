"""C11: replay evaluation report and both gates."""

import argparse
import json
import shutil
from types import SimpleNamespace

import pytest

from purchase_cycle.config import RECORDINGS_PATH
from purchase_cycle.evaluation import harness
from purchase_cycle.evaluation.dataset import CONTRAST_PATH, load_dataset
from purchase_cycle.evaluation.planning import read_jsonl


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


def test_dev_split_skips_the_regression_check(capsys):
    # The stored baseline holds per-case results of the test split only.
    assert harness.evaluate("replay", "dev") == 0
    out = capsys.readouterr().out
    assert "regression vs baseline: skipped (the baseline holds the test split, not dev)" in out
    assert "GATE FAILED" not in out


def test_set_baseline_below_threshold_keeps_the_stored_baseline(baseline_copy, monkeypatch, capsys):
    path = baseline_copy()
    before = path.read_bytes()
    monkeypatch.setattr(harness, "DEFAULT_THRESHOLD", 0.9999)
    assert harness.evaluate("replay", "test", baseline_path=path, set_baseline=True) == 3
    assert path.read_bytes() == before
    assert "the stored baseline was not changed" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_set_baseline_needs_record_mode(mode, capsys):
    args = argparse.Namespace(suite="order_line_extraction", mode=mode, split="test", set_baseline=True, workers=1)
    assert harness.cmd_eval(args) == 2
    assert "--mode record" in capsys.readouterr().err


def test_untraced_runs_disable_tracing_in_the_workers(tmp_path, monkeypatch):
    # Even with tracing switched on in the environment, dev and contrast runs stay untraced.
    from langsmith import utils

    monkeypatch.setattr(utils, "get_env_var", lambda *args, **kwargs: "true")
    assert utils.tracing_is_enabled() is True
    seen = []

    def fake_run_one(graph, case):
        seen.append(utils.tracing_is_enabled())
        return harness.grade(case, case["expected_sku"], case["expected_quantity"])

    monkeypatch.setattr(harness, "_run_one", fake_run_one)
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(c) + "\n" for c in load_dataset()[:3]), encoding="utf-8")
    contrast = tmp_path / "contrast.jsonl"
    contrast.write_text("".join(json.dumps(c) + "\n" for c in read_jsonl(CONTRAST_PATH)[:2]), encoding="utf-8")
    harness.evaluate("replay", "all", dataset_path=dataset, contrast_path=contrast, workers=2)
    assert seen == [False] * 5


class FakeLangSmith:
    remote_ids: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    def has_dataset(self, dataset_name):
        return True

    def list_examples(self, dataset_name):
        split = dataset_name.rsplit("-", 1)[1]
        return [SimpleNamespace(inputs={"case_id": i}) for i in self.remote_ids if i.endswith(split)]

    def evaluate(self, target, data, **kwargs):
        # Like LangSmith, log target exceptions and carry on.
        for case_id in self.remote_ids:
            try:
                target({"case_id": case_id})
            except Exception:
                pass
        return SimpleNamespace(experiment_name="fake")


def test_upload_fails_when_the_remote_dataset_differs(tmp_path, monkeypatch, capsys):
    cases = [{"id": "A-dev", "split": "dev"}, {"id": "B-test", "split": "test"}]
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    monkeypatch.setattr("langsmith.Client", FakeLangSmith)
    monkeypatch.setattr(FakeLangSmith, "remote_ids", ["A-dev", "B-test"])
    assert harness.upload_datasets(dataset) == 0
    monkeypatch.setattr(FakeLangSmith, "remote_ids", ["A-dev", "C-test"])
    assert harness.upload_datasets(dataset) == 1
    assert "case ids differ" in capsys.readouterr().err


def test_experiment_reports_target_errors_apart_from_missing_cases(monkeypatch):
    cases = [harness_case(i) for i in ("X-1", "X-2", "X-3")]

    def fake_run_one(graph, case):
        if case["id"] == "X-1":
            raise ValueError("boom")
        return harness.grade(case, case["expected_sku"], case["expected_quantity"])

    monkeypatch.setattr("langsmith.Client", FakeLangSmith)
    monkeypatch.setattr(FakeLangSmith, "remote_ids", ["X-1", "X-2"])
    monkeypatch.setattr(harness, "_run_one", fake_run_one)
    with pytest.raises(RuntimeError) as raised:
        harness.run_experiment(None, cases, "test", "live", 1)
    message = str(raised.value)
    assert "1 cases raised in the target, first X-1: ValueError('boom')" in message
    assert "lacks 1 local cases" in message


def harness_case(case_id: str) -> dict:
    return {"id": case_id, "category": "exact_name", "expected_sku": "GLV-NIT-M", "expected_quantity": 1}

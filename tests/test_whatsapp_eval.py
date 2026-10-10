"""C13, C15, C20 (phase 05): the `whatsapp_order_extraction` evaluation, its report and gates, on stub answers."""

import argparse
import json

import pytest

from purchase_cycle import db
from purchase_cycle.evaluation import harness, whatsapp_eval
from purchase_cycle.evaluation import whatsapp_dataset as wd
from purchase_cycle.llm import WHATSAPP_EXTRACTION, WHATSAPP_INTAKE, build_system_prompt, recording_key
from purchase_cycle.whatsapp_order import model_text

ORDER_MESSAGES_PER_CATEGORY = 2
NOT_ORDERS = 6  # enough flipped messages for a significant McNemar drop (p = 2 * 0.5**6)


def _subset() -> list[dict]:
    test = [c for c in wd.load_dataset() if c["split"] == "test"]
    cases = [c for c in test if c["category"] == "not_an_order"][:NOT_ORDERS]
    for category in wd.ORDER_CATEGORIES:
        cases += [c for c in test if c["category"] == category][:ORDER_MESSAGES_PER_CATEGORY]
    return [{**c, "file": str(wd.DATASET_DIR / c["file"])} for c in cases]


def perfect_lines(case: dict) -> list[dict]:
    return [
        {
            "source": "message",
            "source_text": line["text"],
            "sku": line["expected_sku"],
            "quantity": line["expected_quantity"],
        }
        for line in case["lines"]
    ]


@pytest.fixture
def suite(tmp_path):
    cases = _subset()
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    conn = db.connect(tmp_path / "catalog.db")
    db.seed(conn)
    catalog = db.catalog_rows(conn)
    conn.close()
    prompts = {task.name: build_system_prompt(catalog, task) for task in (WHATSAPP_INTAKE, WHATSAPP_EXTRACTION)}
    recordings = tmp_path / "recordings.jsonl"

    def write(intake=None, lines=None):
        intake, lines = intake or {}, lines or {}
        rows = []
        for c in cases:
            text = model_text({"body": c["body"]})
            is_order = intake.get(c["id"], c["is_order"])
            answers = [(WHATSAPP_INTAKE, {"is_order": is_order, "reason": "stub"})]
            if is_order:
                answers.append((WHATSAPP_EXTRACTION, {"lines": lines.get(c["id"], perfect_lines(c))}))
            for task, answer in answers:
                key = recording_key(prompts[task.name], text, task)
                rows.append({"key": key, "case_id": c["id"], "sentence": text, "answer": answer})
        recordings.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def run(**kwargs):
        return whatsapp_eval.evaluate(
            "replay",
            "test",
            dataset_path=dataset,
            intake_recordings_path=recordings,
            extraction_recordings_path=recordings,
            baseline_path=tmp_path / "baseline.json",
            workers=2,
            **kwargs,
        )

    run.cases, run.write, run.baseline = cases, write, tmp_path / "baseline.json"
    return run


def _set_baseline(suite, threshold=None):
    suite.write()
    assert suite(set_baseline=True) == 0
    if threshold is not None:
        data = json.loads(suite.baseline.read_text(encoding="utf-8"))
        data["threshold"] = threshold
        suite.baseline.write_text(json.dumps(data), encoding="utf-8")


def _orders(suite):
    return [c for c in suite.cases if c["is_order"]]


def _row(out, metric):
    return next(line for line in out.splitlines() if line.startswith(metric + " "))


def test_perfect_answers_pass_and_report_every_metric_globally_and_per_category(suite, capsys):
    _set_baseline(suite)
    capsys.readouterr()
    assert suite() == 0
    out = capsys.readouterr().out
    assert out.startswith("whatsapp_order_extraction  mode=replay  split=test  messages=14")
    for metric in whatsapp_eval.METRICS:
        assert _row(out, metric)
    for metric, unit in (("intake_accuracy", "message"), ("line_recall", "expected_line")):
        cells = _row(out, metric).split()
        assert unit in cells and ">=95.0%" in cells and "met" in cells and cells[-1] == "PASS"
    assert _row(out, "line_precision").split()[-1] == "reported"
    for category in wd.CATEGORIES:
        assert f"  {category} " in out
    assert "no significant drop" in out
    assert "failures: 0" in out


def test_without_baseline_the_gates_fail(suite, capsys):
    suite.write()
    assert suite() == 1
    assert "GATE FAILED: no threshold set in the baseline" in capsys.readouterr().out


def test_intake_accuracy_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write(intake={c["id"]: True for c in suite.cases if not c["is_order"]})
    assert suite() == 1
    assert "GATE FAILED: intake_accuracy" in capsys.readouterr().out


def test_line_recall_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write(lines={c["id"]: [] for c in _orders(suite)[:3]})
    assert suite() == 1
    assert "GATE FAILED: line_recall" in capsys.readouterr().out


def test_intake_regression_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite, threshold=0.0)
    suite.write(intake={c["id"]: True for c in suite.cases if not c["is_order"]})
    assert suite() == 1
    assert "GATE FAILED: intake_accuracy dropped against the baseline" in capsys.readouterr().out


def test_line_recall_regression_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite, threshold=0.0)
    suite.write(lines={c["id"]: [] for c in _orders(suite)})
    assert suite() == 1
    assert "GATE FAILED: line_recall dropped against the baseline" in capsys.readouterr().out


def test_missing_recordings_exit_two(suite, capsys):
    assert suite() == 2
    assert "--mode record" in capsys.readouterr().err


def test_set_baseline_below_threshold_stops_with_exit_three_and_writes_nothing(suite, capsys):
    suite.write(lines={c["id"]: [] for c in _orders(suite)})
    assert suite(set_baseline=True) == 3
    assert not suite.baseline.exists()
    assert "STOP:" in capsys.readouterr().out


def test_graders_match_whatsapp_lines_one_to_one():
    lines = [
        {"line_id": "W1-L1", "expected_sku": "A", "expected_quantity": 2, "location": "message", "text": "2 a"},
        {"line_id": "W1-L2", "expected_sku": "A", "expected_quantity": 2, "location": "message", "text": "2 a"},
        {
            "line_id": "W1-L3",
            "expected_sku": None,
            "expected_quantity": 1,
            "location": "message",
            "text": "rubber duck",
        },
    ]
    case = {"id": "W1", "category": "short_list", "source": "message", "is_order": True, "lines": lines}
    got = [
        {"source": "message", "source_text": "2 a", "sku": "A", "quantity": 2},
        {"source": "message", "source_text": "1 rubber duck", "sku": None, "quantity": 1},
    ]
    result = whatsapp_eval.grade(case, True, got)
    assert [row["line_recall"] for row in result["line_rows"]] == [True, False]  # one produced line, one credit
    assert result["out_of_catalog_detection"] and not result["email_exact_match"]
    false_source = [{**got[0], "source": "body"}, got[0], got[1]]
    assert [row["line_recall"] for row in whatsapp_eval.grade(case, True, false_source)["line_rows"]] == [True, False]


def test_eval_all_runs_the_whatsapp_suite_after_its_baseline(monkeypatch):
    ran = []
    monkeypatch.setattr(
        harness, "_suite", lambda name: argparse.Namespace(evaluate=lambda *a, **k: ran.append(name) or 0)
    )
    args = argparse.Namespace(suite="all", mode="replay", split="test", set_baseline=False, workers=1)
    assert harness.cmd_eval(args) == 0
    assert ran == [
        "order_line_extraction",
        "web_form_matching",
        "email_order_extraction",
        "clarification_detection",
        "clarification_answers",
        "whatsapp_order_extraction",
        "channel_routing",
        "failure_recovery",
        "order_scenarios",
    ]
    ran.clear()
    args.suite = ["whatsapp_order_extraction"]
    assert harness.cmd_eval(args) == 0
    assert ran == ["whatsapp_order_extraction"]


def test_eval_upload_includes_the_whatsapp_suite(monkeypatch):
    uploaded = []
    monkeypatch.setattr(
        harness, "_suite", lambda name: argparse.Namespace(upload_datasets=lambda: uploaded.append(name) or 0)
    )
    assert harness.cmd_upload(argparse.Namespace(suite="all")) == 0
    assert "whatsapp_order_extraction" in uploaded
    assert "channel_routing" not in uploaded  # deterministic, local only
    assert "failure_recovery" not in uploaded

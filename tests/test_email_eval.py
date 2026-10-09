"""C11: email order extraction evaluation report and gates, run on dataset emails with stub recorded answers."""

import argparse
import json

import pytest

from purchase_cycle import db
from purchase_cycle.email_order import model_text, parse_email
from purchase_cycle.evaluation import clarification_eval, email_eval, harness, web_form_eval
from purchase_cycle.evaluation import email_dataset as ed
from purchase_cycle.llm import EMAIL_EXTRACTION, EMAIL_INTAKE, build_system_prompt, recording_key

ORDER_CATEGORIES_PER_TEST = 2
NOT_ORDERS_PER_TEST = 6  # enough flipped emails for a significant McNemar drop (p = 2 * 0.5**6)


def _subset() -> list[dict]:
    test = [c for c in ed.load_dataset() if c["split"] == "test"]
    cases = [c for c in test if c["category"] == "not_an_order"][:NOT_ORDERS_PER_TEST]
    for category in ed.ORDER_CATEGORIES:
        cases += [c for c in test if c["category"] == category][:ORDER_CATEGORIES_PER_TEST]
    # Absolute paths keep the rendered emails in place while the dataset file lives in tmp.
    return [{**c, "file": str(ed.DATASET_DIR / c["file"])} for c in cases]


def perfect_lines(case: dict) -> list[dict]:
    return [
        {
            "source": line["location"],
            "source_text": line["text"],
            "sku": line["expected_sku"],
            "quantity": line["expected_quantity"],
        }
        for line in case["lines"]
    ]


@pytest.fixture
def suite(tmp_path):
    """Write a small dataset and stub answers for it; returns a runner of the evaluation."""
    cases = _subset()
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    conn = db.connect(tmp_path / "catalog.db")
    db.seed(conn)
    catalog = db.catalog_rows(conn)
    conn.close()
    skus = [row["sku"] for row in catalog]
    prompts = {task.name: build_system_prompt(catalog, task) for task in (EMAIL_INTAKE, EMAIL_EXTRACTION)}
    texts = {c["id"]: model_text(parse_email(open(c["file"], "rb").read())) for c in cases}
    recordings = tmp_path / "recordings.jsonl"

    def write(intake: dict | None = None, lines: dict | None = None):
        """Stub answers; `intake` and `lines` override the perfect answer of chosen email ids."""
        intake, lines = intake or {}, lines or {}
        rows = []
        for c in cases:
            is_order = intake.get(c["id"], c["is_order"])
            answers = [(EMAIL_INTAKE, {"is_order": is_order, "reason": "stub"})]
            if is_order:
                answers.append((EMAIL_EXTRACTION, {"lines": lines.get(c["id"], perfect_lines(c))}))
            for task, answer in answers:
                key = recording_key(prompts[task.name], texts[c["id"]], task)
                rows.append({"key": key, "case_id": c["id"], "sentence": texts[c["id"]], "answer": answer})
        recordings.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def run(baseline=None, **kwargs):
        return email_eval.evaluate(
            "replay",
            "test",
            dataset_path=dataset,
            intake_recordings_path=recordings,
            extraction_recordings_path=recordings,
            baseline_path=baseline or tmp_path / "baseline.json",
            workers=2,
            **kwargs,
        )

    run.cases, run.write, run.skus, run.baseline = cases, write, skus, tmp_path / "baseline.json"
    return run


def _set_baseline(suite, threshold=None):
    suite.write()
    assert suite(set_baseline=True) == 0
    if threshold is not None:
        data = json.loads(suite.baseline.read_text(encoding="utf-8"))
        data["threshold"] = threshold
        suite.baseline.write_text(json.dumps(data), encoding="utf-8")


def _order(suite, category):
    return next(c for c in suite.cases if c["category"] == category)


def test_perfect_answers_pass_and_report_every_metric(suite, capsys):
    suite.write()
    assert suite() == 1  # no baseline yet: no threshold
    assert "GATE FAILED: no threshold set in the baseline" in capsys.readouterr().out
    _set_baseline(suite)
    out = capsys.readouterr().out
    assert f"email_order_extraction  mode=replay  split=test  emails={len(suite.cases)}" in out
    for metric in email_eval.METRICS:
        assert any(line.startswith(metric) and "100.0%" in line for line in out.splitlines())
    for heading in ("95% CI", "per category:", "per source:", "failures: 0", "regression vs baseline"):
        assert heading in out
    for group in (*ed.CATEGORIES, *email_eval.SOURCES):
        assert f"  {group} " in out
    stored = json.loads(suite.baseline.read_text(encoding="utf-8"))
    assert set(stored["emails"]) == {c["id"] for c in suite.cases}
    assert set(stored["lines"]) == {line["line_id"] for c in suite.cases for line in c["lines"] if line["expected_sku"]}
    assert suite() == 0


def test_intake_accuracy_gate_failure_exits_non_zero(suite, capsys):
    _set_baseline(suite)
    not_order = suite.cases[0]["id"]
    suite.write(intake={not_order: True}, lines={not_order: []})
    assert suite() == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: intake_accuracy" in out
    assert "GATE FAILED: line_" not in out and "dropped against the baseline" not in out


def test_line_recall_gate_failure_exits_non_zero(suite, capsys):
    _set_baseline(suite)
    # Drop up to five catalog lines: enough to go under 95%, too few for a significant McNemar drop.
    case = max(
        (c for c in suite.cases if c["is_order"]),
        key=lambda c: min(5, sum(bool(line["expected_sku"]) for line in c["lines"])),
    )
    dropped = [line["line_id"] for line in case["lines"] if line["expected_sku"]][:5]
    suite.write(
        lines={
            case["id"]: [
                g for g, line in zip(perfect_lines(case), case["lines"], strict=True) if line["line_id"] not in dropped
            ]
        }
    )
    assert suite() == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: line_recall" in out
    assert "GATE FAILED: line_precision" not in out and "GATE FAILED: intake" not in out


def test_line_precision_gate_failure_exits_non_zero(suite, capsys):
    _set_baseline(suite)
    case = _order(suite, "pdf_attachment")
    expected = {line["expected_sku"] for line in case["lines"]}
    extra = [sku for sku in suite.skus if sku not in expected][:2]
    added = [{"source": case["attachment"], "source_text": "extra", "sku": sku, "quantity": 1} for sku in extra]
    suite.write(lines={case["id"]: perfect_lines(case) + added})
    assert suite() == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: line_precision" in out
    assert "GATE FAILED: line_recall" not in out and "GATE FAILED: intake" not in out


def test_intake_regression_gate_failure_exits_non_zero(suite, capsys):
    _set_baseline(suite, threshold=0.0)
    flipped = [c["id"] for c in suite.cases if not c["is_order"]]
    suite.write(intake={i: True for i in flipped}, lines={i: [] for i in flipped})
    assert suite() == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: intake_accuracy dropped against the baseline (lost 6, gained 0" in out
    assert "line_recall dropped" not in out


def test_line_recall_regression_gate_failure_exits_non_zero(suite, capsys):
    _set_baseline(suite, threshold=0.0)
    orders = [c for c in suite.cases if c["is_order"]]
    suite.write(lines={c["id"]: [] for c in orders})
    assert suite() == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: line_recall dropped against the baseline" in out
    assert "intake_accuracy dropped" not in out


def test_missing_recordings_exit_with_error(suite, capsys):
    assert suite() == 2
    assert "--mode record" in capsys.readouterr().err


def test_set_baseline_below_threshold_stops_without_writing(suite, capsys):
    suite.write(lines={c["id"]: [] for c in suite.cases if c["is_order"]})
    assert suite(set_baseline=True) == 3
    assert not suite.baseline.exists()
    assert "STOP:" in capsys.readouterr().out


def _case(lines, is_order=True):
    return {"id": "E1", "category": "body_list", "source": "body", "is_order": is_order, "lines": lines}


def _expected(n, sku, quantity, location="body", text="x"):
    return {
        "line_id": f"E1-L{n}",
        "expected_sku": sku,
        "expected_quantity": quantity,
        "location": location,
        "text": text,
    }


def _got(sku, quantity, source="body", text="x"):
    return {"source": source, "source_text": text, "sku": sku, "quantity": quantity}


def test_graders_split_sku_and_quantity_and_count_unmatched_lines():
    case = _case([_expected(1, "A", 2), _expected(2, "B", 3), _expected(3, None, 1)])
    result = email_eval.grade(case, True, [_got("A", 2), _got("B", 5), _got("C", 1), _got(None, 1)])
    assert [r["line_recall"] for r in result["line_rows"]] == [True, False]
    assert [r["field_sku"] for r in result["line_rows"]] == [True, True]
    assert [r["field_quantity"] for r in result["line_rows"]] == [True, False]
    assert (result["precision_hits"], result["produced_catalog"]) == (1, 3)
    assert result["out_of_catalog_detection"] and not result["email_exact_match"]
    summary = email_eval.summarise([result])
    assert summary["field_sku"]["value"] == 1.0 and summary["field_quantity"]["value"] == 0.5
    exact = email_eval.grade(case, True, [_got("A", 2), _got("B", 3), _got(None, 1)])
    assert exact["email_exact_match"] and exact["intake_accuracy"]


def test_grader_counts_an_omitted_line_against_recall_and_exact_match():
    case = _case([_expected(1, "A", 2), _expected(2, "B", 3)])
    result = email_eval.grade(case, True, [_got("A", 2)])
    assert [r["line_recall"] for r in result["line_rows"]] == [True, False]
    assert (result["precision_hits"], result["produced_catalog"]) == (1, 1)
    assert not result["email_exact_match"]


def test_grader_matches_repeated_lines_one_to_one():
    case = _case([_expected(1, "A", 2), _expected(2, "A", 2)])
    result = email_eval.grade(case, True, [_got("A", 2)])
    assert [r["line_recall"] for r in result["line_rows"]] == [True, False]
    assert [r["field_sku"] for r in result["line_rows"]] == [True, False]
    assert not result["email_exact_match"]
    duplicate = email_eval.grade(_case([_expected(1, "A", 2)]), True, [_got("A", 2), _got("A", 2)])
    assert (duplicate["precision_hits"], duplicate["produced_catalog"]) == (1, 2)
    assert not duplicate["email_exact_match"]


def test_sku_only_credit_goes_to_produced_lines_left_after_exact_matches():
    case = _case([_expected(1, "A", 5), _expected(2, "A", 3)])
    result = email_eval.grade(case, True, [_got("A", 3)])
    assert [r["line_recall"] for r in result["line_rows"]] == [False, True]
    assert [r["field_sku"] for r in result["line_rows"]] == [False, True]
    assert [r["field_quantity"] for r in result["line_rows"]] == [False, True]


def test_grader_counts_an_invented_line_against_precision_and_exact_match():
    case = _case([_expected(1, "A", 2)])
    result = email_eval.grade(case, True, [_got("A", 2), _got("Z", 1)])
    assert result["line_rows"][0]["line_recall"]
    assert (result["precision_hits"], result["produced_catalog"]) == (1, 2)
    assert not result["email_exact_match"]
    unknown = email_eval.grade(case, True, [_got("A", 2), _got(None, 1)])
    assert not unknown["out_of_catalog_detection"] and not unknown["email_exact_match"]


def test_grader_fails_a_wrong_quantity_on_catalog_and_unknown_lines():
    case = _case([_expected(1, "A", 2), _expected(2, None, 4)])
    catalog = email_eval.grade(case, True, [_got("A", 3), _got(None, 4)])
    assert not catalog["line_rows"][0]["line_recall"] and catalog["line_rows"][0]["field_sku"]
    assert not catalog["email_exact_match"]
    unknown = email_eval.grade(case, True, [_got("A", 2), _got(None, 5)])
    assert unknown["line_rows"][0]["line_recall"]
    assert not unknown["out_of_catalog_detection"] and not unknown["email_exact_match"]


def test_grader_fails_a_false_source_on_catalog_and_unknown_lines():
    case = _case([_expected(1, "A", 2, "order.pdf"), _expected(2, None, 4, "order.pdf")])
    catalog = email_eval.grade(case, True, [_got("A", 2, "body"), _got(None, 4, "order.pdf")])
    assert not catalog["line_rows"][0]["line_recall"] and catalog["line_rows"][0]["field_quantity"]
    assert catalog["precision_hits"] == 0 and not catalog["email_exact_match"]
    unknown = email_eval.grade(case, True, [_got("A", 2, "order.pdf"), _got(None, 4, "body")])
    assert unknown["line_rows"][0]["line_recall"]
    assert not unknown["out_of_catalog_detection"] and not unknown["email_exact_match"]
    right = email_eval.grade(case, True, [_got("A", 2, "order.pdf"), _got(None, 4, "order.pdf")])
    assert right["email_exact_match"]


def test_out_of_catalog_text_prefers_the_longest_expected_phrase_and_pairs_optimally():
    case = _case([_expected(1, None, 2, text="FFP1 masks"), _expected(2, None, 2, text="FFP1 masks, box")])
    produced = [_got(None, 2, text="FFP1 masks, box"), _got(None, 2, text="FFP1 masks")]
    result = email_eval.grade(case, True, produced)
    assert result["unmatched_hits"] == 2 and result["out_of_catalog_detection"] and result["email_exact_match"]


def test_out_of_catalog_citation_copying_several_expected_texts_matches_nothing():
    case = _case([_expected(1, None, 2, text="Stair lifts"), _expected(2, None, 2, text="Patient lifts")])
    table = "Stair lifts 2 / Patient lifts 2"
    result = email_eval.grade(case, True, [_got(None, 2, text=table), _got(None, 2, text=table)])
    assert result["unmatched_hits"] == 0 and not result["out_of_catalog_detection"]


def test_out_of_catalog_text_matches_whole_words_only():
    case = _case([_expected(1, None, 1, text="gel")])
    result = email_eval.grade(case, True, [_got(None, 1, text="Angel wings")])
    assert result["unmatched_hits"] == 0 and not result["out_of_catalog_detection"]


def test_out_of_catalog_produced_line_counts_for_one_expected_line():
    case = _case([_expected(1, None, 3, text="Syringes 60 ml"), _expected(2, None, 3, text="Syringes 60 ml")])
    produced = [_got(None, 3, text="Syringes 60 ml"), _got(None, 3, text="Hospital beds")]
    result = email_eval.grade(case, True, produced)
    assert result["unmatched_hits"] == 1
    assert not result["out_of_catalog_detection"] and not result["email_exact_match"]


def test_a_stopped_run_keeps_the_intake_decision_and_counts_no_lines():
    result = email_eval.grade(_case([_expected(1, "A", 2)]), True, [], "invalid answer")
    assert result["intake_accuracy"] and not result["line_rows"][0]["line_recall"]
    assert not email_eval.grade(_case([], is_order=False), None, [], "unknown sender")["intake_accuracy"]


def test_eval_command_runs_every_suite_and_keeps_the_worst_exit(monkeypatch):
    monkeypatch.setattr(harness, "evaluate", lambda *args, **kwargs: 0)
    monkeypatch.setattr(web_form_eval, "evaluate", lambda *args, **kwargs: 0)
    monkeypatch.setattr(email_eval, "evaluate", lambda *args, **kwargs: 1)
    monkeypatch.setattr(clarification_eval.detection, "evaluate", lambda *args, **kwargs: 0)
    monkeypatch.setattr(clarification_eval.answers, "evaluate", lambda *args, **kwargs: 0)
    args = argparse.Namespace(suite="all", mode="replay", split="test", set_baseline=False, workers=1)
    assert harness.cmd_eval(args) == 1
    args.suite = "web_form_matching"
    assert harness.cmd_eval(args) == 0

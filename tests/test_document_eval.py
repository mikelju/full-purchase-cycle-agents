"""C11 (phase 08): the `document_extraction` evaluation, its report and gates, on hand-written recordings."""

import argparse
import json
import shutil

import pytest

from purchase_cycle.evaluation import document_dataset as dd
from purchase_cycle.evaluation import document_eval, harness
from purchase_cycle.llm import SUPPLIER_DOCUMENT_EXTRACTION, build_system_prompt, recording_key

PER_CATEGORY = 3
FLIPPED = 6  # enough flipped documents for a significant McNemar drop (p = 2 * 0.5**6)


def _subset() -> list[dict]:
    test = [c for c in dd.load_dataset() if c["split"] == "test"]
    cases = []
    for category in dd.CATEGORIES:
        cases += [c for c in test if c["category"] == category][:PER_CATEGORY]
    return cases


def perfect(case: dict) -> dict:
    return {
        "kind": case["kind"],
        "number": case["number"],
        "po_reference": case["reference_written"],
        "issue_date": case["issue_date"],
        "currency": case["currency"],
        "supplier_text": case["supplier_written"],
        "lines": [
            {
                "product": line["sku"],
                "quantity": line["quantity"],
                "unit_price": line["unit_price"],
                "line_total": line["line_total"],
            }
            for line in case["lines"]
        ],
    }


def _with_line(case, change):
    answer = perfect(case)
    answer["lines"][0] = {**answer["lines"][0], **change}
    return answer


def _price_off(case):
    return _with_line(case, {"unit_price": case["lines"][0]["unit_price"] + 1})


@pytest.fixture
def suite(tmp_path):
    cases = _subset()
    (tmp_path / "files").mkdir()
    for c in cases:
        if "file" in c:
            shutil.copy(dd.DATASET_DIR / c["file"], tmp_path / c["file"])
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    prompt = build_system_prompt([], SUPPLIER_DOCUMENT_EXTRACTION)
    recordings = tmp_path / "recordings.jsonl"

    def write(answers=None):
        answers = answers or {}
        rows = []
        for c in cases:
            text = document_eval.model_input(c, tmp_path)
            key = recording_key(prompt, text, SUPPLIER_DOCUMENT_EXTRACTION)
            rows.append({"key": key, "case_id": c["id"], "sentence": text, "answer": answers.get(c["id"], perfect(c))})
        recordings.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def run(**kwargs):
        return document_eval.evaluate(
            "replay",
            "test",
            dataset_path=dataset,
            recordings_path=recordings,
            baseline_path=tmp_path / "baseline.json",
            workers=2,
            **kwargs,
        )

    run.cases, run.write, run.baseline, run.dir = cases, write, tmp_path / "baseline.json", tmp_path
    return run


def _set_baseline(suite):
    suite.write()
    assert suite(set_baseline=True) == 0


def _row(out, metric):
    return next(line for line in out.splitlines() if line.startswith(metric + " "))


def _invoices(suite):
    return [c for c in suite.cases if c["kind"] == "invoice"]


def test_the_dataset_has_pdf_and_excel_documents_read_through_the_product_readers():
    rendered = [c for c in dd.load_dataset() if c["format"] != "txt"]
    assert {c["format"] for c in rendered} == {"pdf", "xlsx"}
    for c in rendered[:4]:
        text = document_eval.model_input(c, dd.DATASET_DIR)
        assert text.startswith("Supplier document:\n") and c["number"] in text


def test_perfect_answers_pass_and_report_every_metric_globally_and_per_category(suite, capsys):
    _set_baseline(suite)
    capsys.readouterr()
    suite.write()
    assert suite() == 0
    out = capsys.readouterr().out
    for metric in document_eval.METRICS:
        assert _row(out, metric)
    assert "100.0%" in _row(out, "exact_match")
    assert "[" in _row(out, "unit_price_accuracy")  # Wilson interval
    assert "0 of" in _row(out, "critical_errors") and "Wilson upper bound" in _row(out, "critical_errors")
    assert "no significant drop" in out
    for category in dd.CATEGORIES:
        assert category in out
    per_category = out.split("per category:")[1]
    for metric in document_eval.METRICS:
        assert metric in per_category


def test_without_baseline_the_gates_fail(suite, capsys):
    suite.write()
    assert suite() == 1
    assert "no baseline stored" in capsys.readouterr().out


def test_exact_match_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write({c["id"]: {**perfect(c), "po_reference": "PO-9999"} for c in suite.cases[:4]})
    assert suite() == 1
    assert "GATE FAILED: exact_match" in capsys.readouterr().out


def test_unit_price_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write({c["id"]: _price_off(c) for c in _invoices(suite)[:3]})
    assert suite() == 1
    assert "GATE FAILED: unit_price_accuracy" in capsys.readouterr().out


def test_exact_match_regression_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write({c["id"]: {**perfect(c), "po_reference": None} for c in suite.cases[:FLIPPED]})
    assert suite() == 1
    assert "exact_match dropped against the baseline" in capsys.readouterr().out


def test_unit_price_regression_gate_failure_exits_one(suite, capsys):
    _set_baseline(suite)
    suite.write({c["id"]: _price_off(c) for c in _invoices(suite)[:FLIPPED]})
    assert suite() == 1
    assert "unit_price_accuracy dropped against the baseline" in capsys.readouterr().out


def test_missing_recordings_exit_two(suite, capsys):
    assert suite() == 2
    assert "No recording" in capsys.readouterr().err


def test_set_baseline_below_threshold_stops_with_exit_three_and_writes_nothing(suite, capsys):
    suite.write({c["id"]: _price_off(c) for c in _invoices(suite)[:3]})
    assert suite(set_baseline=True) == 3
    assert "STOP" in capsys.readouterr().out
    assert not suite.baseline.exists()


def test_schema_failure_grades_as_extraction_failed(suite, capsys):
    _set_baseline(suite)
    bad = suite.cases[0]
    suite.write({bad["id"]: {"kind": "receipt"}})
    suite()
    assert f"{bad['id']}  extraction_failed" in capsys.readouterr().out


def test_a_failed_product_check_grades_as_extraction_failed():
    from types import SimpleNamespace

    from purchase_cycle.llm import SupplierDocument

    case = next(c for c in dd.load_dataset() if c["kind"] == "invoice" and c["format"] == "txt")
    answer = SupplierDocument.model_validate({**perfect(case), "currency": "USD"})
    client = SimpleNamespace(extract=lambda text, case_id=None: answer)
    r = document_eval.run_document(client, case, dd.DATASET_DIR)
    assert r["error"].startswith("DocumentRejected") and r["counts"]["critical_errors"] == [1, 1]
    assert r["counts"]["exact_match"] == [0, 1]


def test_grade_matches_lines_by_sku_resolving_product_text_like_the_product():
    case = next(c for c in dd.load_dataset() if c["kind"] == "invoice" and c["lines"][0]["written_as"] == "name")
    by_name = perfect(case)
    by_name["lines"] = [{**by_name["lines"][0], "product": case["lines"][0]["product"]}, *by_name["lines"][1:]]
    r = document_eval.grade(case, by_name)
    assert r["counts"]["exact_match"] == [1, 1] and r["counts"]["critical_errors"] == [0, 1]
    n = len(case["lines"])
    assert r["counts"]["line_recall"] == [n, n] and r["counts"]["line_precision"] == [n, n]


def test_reference_forms_normalise_to_the_canonical_reference():
    case = next(c for c in dd.load_dataset() if c["reference_written"] != c["po_reference"])
    assert document_eval.grade(case, perfect(case))["counts"]["reference_accuracy"] == [1, 1]
    wrong = {**perfect(case), "po_reference": "PO-9999"}
    assert document_eval.grade(case, wrong)["counts"]["reference_accuracy"] == [0, 1]


def test_half_cent_price_tolerance_and_line_level_critical_errors():
    case = next(c for c in dd.load_dataset() if c["kind"] == "invoice" and len(c["lines"]) >= 2)
    n = len(case["lines"])
    price = case["lines"][0]["unit_price"]
    near = document_eval.grade(case, _with_line(case, {"unit_price": price + 0.004}))
    assert near["counts"]["exact_match"] == [1, 1] and near["counts"]["critical_errors"] == [0, 1]
    far = document_eval.grade(case, _with_line(case, {"unit_price": price + 0.01}))
    assert far["counts"]["exact_match"] == [0, 1] and far["counts"]["critical_errors"] == [1, 1]
    quantity = document_eval.grade(case, _with_line(case, {"quantity": case["lines"][0]["quantity"] + 1}))
    assert quantity["counts"]["critical_errors"] == [1, 1] and quantity["counts"]["quantity_accuracy"][0] == n - 1
    missing = perfect(case)
    missing["lines"] = missing["lines"][1:]
    r = document_eval.grade(case, missing)
    assert r["counts"]["critical_errors"] == [1, 1] and r["counts"]["line_recall"] == [n - 1, n]
    invented = perfect(case)
    invented["lines"].append({"product": "Freight", "quantity": 1.0, "unit_price": 5.0, "line_total": 5.0})
    r = document_eval.grade(case, invented)
    assert r["counts"]["critical_errors"] == [1, 1] and r["counts"]["line_precision"] == [n, n + 1]
    assert r["counts"]["line_recall"] == [n, n]


def test_wrong_reference_breaks_exact_match_but_is_not_critical():
    case = next(c for c in dd.load_dataset() if c["kind"] == "invoice")
    r = document_eval.grade(case, {**perfect(case), "po_reference": None})
    assert r["counts"]["exact_match"] == [0, 1] and r["counts"]["critical_errors"] == [0, 1]


def test_delivery_notes_have_no_price_to_grade():
    case = next(c for c in dd.load_dataset() if c["kind"] == "delivery_note")
    r = document_eval.grade(case, perfect(case))
    assert r["counts"]["exact_match"] == [1, 1] and r["counts"]["unit_price_accuracy"] == [0, 0]


def test_eval_all_runs_the_suite_last_once_its_baseline_is_recorded(monkeypatch):
    assert "document_extraction" in harness.SUITES and "document_extraction" in harness.UPLOAD_SUITES
    assert harness.ALL_SUITES[-1] == "document_extraction"
    ran = []
    monkeypatch.setattr(
        harness, "_suite", lambda name: argparse.Namespace(evaluate=lambda *a, **k: ran.append(name) or 0)
    )
    args = argparse.Namespace(suite="all", mode="replay", split="test", set_baseline=False, workers=1)
    assert harness.cmd_eval(args) == 0
    assert ran[-1] == "document_extraction" and ran.count("document_extraction") == 1
    ran.clear()
    args.suite = ["document_extraction"]
    assert harness.cmd_eval(args) == 0
    assert ran == ["document_extraction"]


def test_eval_upload_includes_the_document_suite(monkeypatch):
    uploaded = []
    monkeypatch.setattr(
        harness, "_suite", lambda name: argparse.Namespace(upload_datasets=lambda: uploaded.append(name) or 0)
    )
    assert harness.cmd_upload(argparse.Namespace(suite="all")) == 0
    assert "document_extraction" in uploaded

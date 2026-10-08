"""C13: clarification detection and answer evaluations, their graders and gates, on fixture results and recordings."""

import argparse
import json

import pytest

from purchase_cycle import db
from purchase_cycle.clarification import AMBIGUOUS, QUANTITY, UNKNOWN
from purchase_cycle.evaluation import clarification_eval as ce
from purchase_cycle.evaluation import harness
from purchase_cycle.evaluation.planning import read_jsonl, write_jsonl
from purchase_cycle.llm import CLARIFICATION_ANSWER, build_system_prompt, recording_key

ANSWER_CASES = 24  # four per category
FLIPPED = 6  # enough lost lines for a significant McNemar drop (p = 2 * 0.5**6)


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "eval.db"
    conn = db.connect(path)
    db.seed(conn)
    rows = db.catalog_rows(conn)
    conn.close()
    return rows


def ideal_lines(case: dict) -> list[dict]:
    """Lines as a perfect matcher or extractor returns them: the planned SKU, or none for a product doubt."""
    if case["channel"] == "web_form":
        return [
            {"product": line["text"], "quantity": line["quantity"], "sku": line["expected_sku"]}
            for line in case["lines"]
        ]
    # An email line with no stated number is read as 1, as the planner assumes.
    return [
        {"source_text": line["text"], "quantity": line["quantity"] or 1, "sku": line["expected_sku"]}
        for line in case["lines"]
    ]


# Graders


def test_ideal_lines_raise_exactly_the_planned_doubts(catalog):
    cases = [c for c in read_jsonl(ce.DETECTION_DATASET) if c["split"] == "test"]
    results = [ce.grade_order(c, ideal_lines(c), catalog) for c in cases]
    summary = ce.summarise_detection(results)
    for metric in (*ce.RECALLS, *ce.PRECISIONS):
        assert summary[metric]["value"] == 1.0 and summary[metric]["n"] >= 40, metric
    assert summary["false_question_rate"]["hits"] == 0 and summary["false_question_rate"]["n"] >= 45
    assert not any(ce.detection_failed(r) for r in results)


def test_email_lines_align_by_text_and_a_stray_doubt_lowers_precision(catalog):
    case = next(c for c in read_jsonl(ce.DETECTION_DATASET) if c["channel"] == "email" and len(c["lines"]) >= 3)
    produced = list(reversed(ideal_lines(case))) + [{"source_text": "2 x-ray film sheets", "quantity": 2, "sku": None}]
    result = ce.grade_order(case, produced, catalog)
    assert all(row["aligned"] for row in result["line_rows"])
    assert all(sorted(row["raised"]) == sorted(row["expected"]) for row in result["line_rows"])
    assert result["stray"] == [UNKNOWN] and ce.detection_failed(result)
    assert ce.summarise_detection([result])["precision_unknown"]["value"] < 1


def test_a_missed_line_misses_its_doubts_and_a_stopped_run_misses_all(catalog):
    case = next(c for c in read_jsonl(ce.DETECTION_DATASET) if c["has_doubt"] and len(c["lines"]) >= 2)
    doubtful = next(i for i, line in enumerate(case["lines"]) if line["expected_doubts"])
    produced = [line for i, line in enumerate(ideal_lines(case)) if i != doubtful]
    row = ce.grade_order(case, produced, catalog)["line_rows"][doubtful]
    assert not row["aligned"] and row["raised"] == []
    stopped = ce.grade_order(case, [], catalog, "invalid answer")
    assert stopped["error"] and not stopped["question"]
    assert ce.summarise_detection([stopped])[f"recall_{case['lines'][doubtful]['expected_doubts'][0]}"]["hits"] == 0


def test_resolution_grader():
    expected = {"action": "set", "sku": "A", "quantity": 3}
    assert ce.resolution_right(expected, {"action": "set", "sku": "A", "quantity": 3})
    assert not ce.resolution_right(expected, {"action": "set", "sku": "A", "quantity": 4})
    assert not ce.resolution_right(expected, None)
    assert ce.resolution_right(
        {"action": "remove", "sku": None, "quantity": None}, {"action": "remove", "sku": "A", "quantity": 1}
    )
    case = {
        "id": "C",
        "channel": "email",
        "category": "several_lines",
        "doubts": [{"line_id": 1, "expected": expected}, {"line_id": 2, "expected": {"action": "unclear"}}],
    }
    graded = ce.grade_case(
        case,
        [
            {"line_id": 1, "action": "set", "sku": "A", "quantity": 3},
            {"line_id": 2, "action": "remove", "sku": None, "quantity": None},
        ],
    )
    assert [row["resolution_accuracy"] for row in graded["line_rows"]] == [True, False]
    assert not graded["case_exact_match"]
    assert not ce.grade_case(case, [], "rejected")["line_rows"][0]["resolution_accuracy"]


# Answer evaluation, end to end in replay with hand-written recordings


def _answer_subset(tmp_path):
    test = [c for c in read_jsonl(ce.ANSWERS_DATASET) if c["split"] == "test"]
    per = ANSWER_CASES // 6
    cases = [c for category in ce.cd.ANSWER_CATEGORIES for c in [c for c in test if c["category"] == category][:per]]
    path = tmp_path / "answers.jsonl"
    write_jsonl(path, cases)
    return path, cases


def _write_answers(path, cases, catalog, wrong: int = 0):
    """Recordings answering every case as expected, except the first `wrong` doubtful lines, removed instead."""
    prompt = build_system_prompt(catalog, CLARIFICATION_ANSWER)
    rows, flipped = [], 0
    for case in cases:
        resolutions = []
        for d in case["doubts"]:
            r = {"line_id": d["line_id"], **d["expected"]}
            if flipped < wrong and r["action"] != "remove":
                r, flipped = {"line_id": d["line_id"], "action": "remove", "sku": None, "quantity": None}, flipped + 1
            resolutions.append(r)
        text = ce.answer_message(ce.case_doubts(case), case["question"], case["answer"])
        key = recording_key(prompt, text, CLARIFICATION_ANSWER)
        rows.append({"key": key, "case_id": case["id"], "sentence": text, "answer": {"resolutions": resolutions}})
    write_jsonl(path, rows)
    return path


def _answers(tmp_path, catalog, wrong=0, **kwargs):
    dataset, cases = _answer_subset(tmp_path)
    recordings = _write_answers(tmp_path / f"rec-{wrong}.jsonl", cases, catalog, wrong)
    return ce.evaluate_answers("replay", "test", dataset_path=dataset, recordings_path=recordings, workers=2, **kwargs)


def test_answers_pass_against_their_own_baseline_and_fail_each_gate(tmp_path, catalog, capsys):
    baseline = tmp_path / "answers.json"
    # Exit 3: the baseline run stays under 95%, and nothing is stored.
    assert _answers(tmp_path, catalog, wrong=FLIPPED, baseline_path=baseline, set_baseline=True) == 3
    assert not baseline.exists() and "STOP:" in capsys.readouterr().out
    # No baseline yet: no threshold, exit 1.
    assert _answers(tmp_path, catalog, baseline_path=baseline) == 1
    assert "GATE FAILED: no threshold set in the baseline" in capsys.readouterr().out
    assert _answers(tmp_path, catalog, baseline_path=baseline, set_baseline=True) == 0
    out = capsys.readouterr().out
    for text in (
        "resolution_accuracy",
        "case_exact_match",
        "per channel:",
        "per category:",
        "pick_variant",
        "failures: 0",
    ):
        assert text in out
    # Exit 1: the absolute gate.
    assert _answers(tmp_path, catalog, wrong=FLIPPED, baseline_path=baseline) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: resolution_accuracy" in out and "below threshold" in out
    # Exit 1: the McNemar gate alone, with a threshold the run still meets.
    data = json.loads(baseline.read_text(encoding="utf-8"))
    baseline.write_text(json.dumps({**data, "threshold": 0.5}), encoding="utf-8")
    assert _answers(tmp_path, catalog, wrong=FLIPPED, baseline_path=baseline) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: resolution_accuracy dropped against the baseline" in out and "below threshold" not in out


def test_answers_exit_2_without_recordings(tmp_path, capsys):
    dataset, _ = _answer_subset(tmp_path)
    assert ce.evaluate_answers("replay", "test", dataset_path=dataset, recordings_path=tmp_path / "none.jsonl") == 2
    assert "--mode record" in capsys.readouterr().err


def test_a_rejected_interpretation_counts_every_line_wrong(tmp_path, catalog, capsys):
    dataset, cases = _answer_subset(tmp_path)
    recordings = _write_answers(tmp_path / "rec.jsonl", cases, catalog)
    rows = read_jsonl(recordings)
    rows[0]["answer"] = {"resolutions": []}  # misses its lines: check_resolutions rejects it
    write_jsonl(recordings, rows)
    ce.evaluate_answers(
        "replay", "test", dataset_path=dataset, recordings_path=recordings, baseline_path=tmp_path / "b.json"
    )
    assert "error: Clarification answer rejected" in capsys.readouterr().out


# Detection evaluation: the channel runs are replaced by ideal lines, then degraded


def _detection(tmp_path, monkeypatch, catalog, degrade=None, **kwargs):
    def fake_run_order(graph, case, _catalog, root):
        result = ce.grade_order(case, ideal_lines(case), catalog)
        return degrade(case, result) if degrade else result

    monkeypatch.setattr(ce, "run_order", fake_run_order)
    return ce.evaluate_detection("replay", "test", workers=2, **kwargs)


def _drop(kind: str, count: int):
    """Drop the raised doubts of the first `count` lines of one doubt type."""
    dropped = []

    def degrade(case, result):
        for row in result["line_rows"]:
            if kind in row["expected"] and len(dropped) < count and row["id"] not in dropped:
                dropped.append(row["id"])
                row["raised"] = []
        return result

    return degrade


def test_detection_passes_against_its_own_baseline_and_fails_each_gate(tmp_path, monkeypatch, catalog, capsys):
    baseline = tmp_path / "detection.json"
    assert _detection(tmp_path, monkeypatch, catalog, baseline_path=baseline, set_baseline=True) == 0
    out = capsys.readouterr().out
    for text in (
        "recall_ambiguous",
        "precision_quantity",
        "false_question_rate",
        "per channel:",
        "web_form",
        "per category (line kind):",
        "over_ceiling",
        "failures: 0",
    ):
        assert text in out
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["threshold"] == 0.95 and len(data["doubts"]) >= 120 and len(data["orders"]) == 150
    # Exit 1: recall of each doubt type under 95%.
    for kind in (AMBIGUOUS, UNKNOWN, QUANTITY):
        assert _detection(tmp_path, monkeypatch, catalog, _drop(kind, 10), baseline_path=baseline) == 1
        assert f"GATE FAILED: recall_{kind}" in capsys.readouterr().out
    # Exit 1: the false question rate above 5%.

    def ask_everyone(case, result):
        if not case["has_doubt"]:
            result["question"] = True
        return result

    assert _detection(tmp_path, monkeypatch, catalog, ask_everyone, baseline_path=baseline) == 1
    assert "GATE FAILED: false_question_rate" in capsys.readouterr().out
    # Exit 1: the McNemar gate alone, with a threshold the run still meets.
    baseline.write_text(json.dumps({**data, "threshold": 0.5}), encoding="utf-8")
    assert _detection(tmp_path, monkeypatch, catalog, _drop(AMBIGUOUS, FLIPPED), baseline_path=baseline) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: detection recall dropped against the baseline" in out and "below threshold" not in out
    # Exit 3: a baseline run under 95% leaves the stored baseline unchanged.
    before = baseline.read_text(encoding="utf-8")
    assert (
        _detection(tmp_path, monkeypatch, catalog, _drop(QUANTITY, 10), baseline_path=baseline, set_baseline=True) == 3
    )
    assert baseline.read_text(encoding="utf-8") == before


def test_detection_exit_2_without_recordings(tmp_path, capsys):
    case = next(
        c
        for c in read_jsonl(ce.DETECTION_DATASET)
        if c["channel"] == "web_form" and c["split"] == "test" and c["has_doubt"]
    )
    dataset = tmp_path / "dataset.jsonl"
    write_jsonl(dataset, [case])
    empty = tmp_path / "none.jsonl"
    code = ce.evaluate_detection(
        "replay",
        "test",
        dataset_path=dataset,
        matching_recordings_path=empty,
        intake_recordings_path=empty,
        extraction_recordings_path=empty,
        baseline_path=tmp_path / "b.json",
    )
    assert code == 2 and "--mode record" in capsys.readouterr().err


def test_eval_command_takes_several_suites_and_set_baseline_needs_one(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(ce.detection, "evaluate", lambda *a, **k: ran.append("detection") or 2)
    monkeypatch.setattr(ce.answers, "evaluate", lambda *a, **k: ran.append("answers") or 1)
    args = argparse.Namespace(
        suite=["clarification_detection", "clarification_answers"],
        mode="replay",
        split="test",
        set_baseline=False,
        workers=1,
    )
    assert harness.cmd_eval(args) == 2 and ran == ["detection", "answers"]
    args.set_baseline, args.mode = True, "record"
    assert harness.cmd_eval(args) == 2 and "one --suite" in capsys.readouterr().err

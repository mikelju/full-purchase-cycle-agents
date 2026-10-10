"""`order_scenarios` suite (phase 05, increment 24 task 3): runner, grader, report and gates, all offline.

The model answers are small fake recordings written by the tests for the scenario texts, so every run is a replay
with the network blocked; nothing reaches Anthropic or LangSmith.
"""

import json
import shutil

from purchase_cycle import db, llm
from purchase_cycle.evaluation import critical, harness
from purchase_cycle.evaluation import scenarios_eval as se

INTAKE = {"is_order": True, "reason": "An order."}
UNITS = {text: unit for unit, text, _ in se.PRODUCTS_WRITTEN.values()}
CLEAN = ("OS-001", "OS-008", "OS-009", "OS-011", "OS-018", "OS-019", "OS-021", "OS-027", "OS-028")


def _catalog() -> list:
    conn = db.connect(":memory:")
    try:
        db.seed(conn)
        return db.catalog_rows(conn)
    finally:
        conn.close()


def _row(catalog, task, sentence, answer) -> dict:
    key = llm.recording_key(llm.build_system_prompt(catalog, task), sentence, task)
    return {"key": key, "case_id": None, "sentence": sentence, "answer": answer}


def _fake_answers(item: dict, tmp_path) -> list[dict]:
    """The answers a right model gives for a scenario with no clarification."""
    catalog = _catalog()
    text = se.write_message(tmp_path / "texts" / item["id"], item)
    quantities = {line["sku"]: line["quantity"] for line in item["expected"]["lines"]}
    pairs = [(r["text"], r["sku"]) for r in item["requested"]]
    if item["channel"] == "web_form":
        return [_row(catalog, llm.MATCHING, product, {"sku": sku}) for product, sku in pairs]
    source = {"whatsapp": "message", "email": "body"}[item["channel"]]
    lines = [
        {
            "source": source,
            "source_text": f"{quantities.get(sku, 1)} {UNITS[product]} of {product}",
            "sku": sku,
            "quantity": quantities.get(sku, 1),
        }  # fmt: skip
        for product, sku in pairs
    ]
    intake, extract = {
        "whatsapp": (llm.WHATSAPP_INTAKE, llm.WHATSAPP_EXTRACTION),
        "email": (llm.EMAIL_INTAKE, llm.EMAIL_EXTRACTION),
    }[item["channel"]]
    return [_row(catalog, intake, text, INTAKE), _row(catalog, extract, text, {"lines": lines})]


def _setup(tmp_path, ids) -> tuple:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    rows = []
    for item_id in ids:
        shutil.copy(se.DATASET_DIR / f"{item_id}.json", dataset)
        rows += _fake_answers(json.loads((dataset / f"{item_id}.json").read_text(encoding="utf-8")), tmp_path)
    recordings = tmp_path / "recordings.jsonl"
    recordings.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return dataset, recordings


def _evaluate(tmp_path, ids, baseline=None) -> int:
    dataset, recordings = _setup(tmp_path, ids)
    baseline_path = baseline or tmp_path / "no_baseline.json"
    return se.evaluate("replay", "test", dataset_dir=dataset, recordings_path=recordings, baseline_path=baseline_path)


def _line(out: str, metric: str) -> str:
    return next(line for line in out.splitlines() if line.startswith(metric + " "))


def test_clean_scenarios_on_every_channel_pass_offline_with_the_report_format(tmp_path, capsys, no_network):
    assert _evaluate(tmp_path, CLEAN) == 0
    out = capsys.readouterr().out
    row = _line(out, "scenario_success").split()
    assert "100.0%" in row and "scenario" in row and ">=90.0%" in row and "met" in row and "PASS" in row
    critical_row = _line(out, "critical_errors").split()
    assert critical_row[1] == "0" and "=0" in critical_row and "PASS" in critical_row
    assert "by kind: duplicate_order=0" in out and "regression vs baseline: no baseline stored" in out
    for group in ("whatsapp", "email", "web_form", "crash_resume", "redelivery"):
        assert f"  {group} " in out
    assert "failures: 0" in out
    assert no_network == []


def test_a_wrong_quantity_fails_the_grader_and_the_gate_with_exit_one(tmp_path, capsys, no_network):
    # The stored order keeps the quantity the customer wrote; a copy of each scenario expects one more on its
    # first line, so the final state misses the expected one (the committed files are not touched).
    dataset, recordings = _setup(tmp_path, ("OS-001", "OS-011"))
    for path in dataset.glob("OS-*.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        item["expected"]["lines"][0]["quantity"] += 1
        path.write_text(json.dumps(item), encoding="utf-8")
    code = se.evaluate("replay", "test", dataset_dir=dataset, recordings_path=recordings,
                       baseline_path=tmp_path / "none.json")  # fmt: skip
    assert code == 1
    out = capsys.readouterr().out
    assert "OS-001  wrong ['lines']" in out and "OS-011  wrong ['lines']" in out
    assert "0 of 2, Wilson upper bound" in out
    assert "GATE FAILED: scenario_success 0.0% below threshold 90.0%" in out


def test_an_injected_critical_error_fails_with_exit_one(tmp_path, capsys, monkeypatch, no_network):
    real = critical.count

    def with_a_dropped_line(*args, **kwargs):
        return {**real(*args, **kwargs), "line_dropped": 1}

    monkeypatch.setattr(critical, "count", with_a_dropped_line)
    assert _evaluate(tmp_path, ("OS-021",)) == 1
    out = capsys.readouterr().out
    assert "OS-021  wrong ['critical']" in out
    assert "GATE FAILED: critical_errors 1 above threshold 0" in out


def test_a_real_dropped_line_in_the_database_fails_with_exit_one(tmp_path, capsys, monkeypatch, no_network):
    # Review round 4: the stored lines are deleted from the scenario database before the real counter reads it.
    real = critical.count
    seen = {}

    def after_losing_the_lines(db_path, *args, **kwargs):
        conn = db.connect(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM order_lines")
        finally:
            conn.close()
        seen.update(real(db_path, *args, **kwargs))
        return seen

    monkeypatch.setattr(critical, "count", after_losing_the_lines)
    assert _evaluate(tmp_path, ("OS-021",)) == 1
    out = capsys.readouterr().out
    assert seen["line_dropped"] > 0
    assert f"line_dropped={seen['line_dropped']}" in out and "'critical'" in _line(out, "  OS-021")
    assert f"GATE FAILED: critical_errors {seen['line_dropped']} above threshold 0" in out
    assert no_network == []


def test_set_baseline_below_the_target_stops_without_writing(tmp_path, capsys, no_network):
    dataset, recordings = _setup(tmp_path, ("OS-001", "OS-011"))
    for path in dataset.glob("OS-*.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        item["expected"]["lines"][0]["quantity"] += 1
        path.write_text(json.dumps(item), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    code = se.evaluate("replay", "test", dataset_dir=dataset, recordings_path=recordings, baseline_path=baseline,
                       set_baseline=True)  # fmt: skip
    assert code == 3
    assert "STOP: the scenarios did not reach the 90% target" in capsys.readouterr().out
    assert not baseline.exists()
    assert no_network == []


def test_set_baseline_with_a_critical_error_stops_without_writing(tmp_path, capsys, monkeypatch, no_network):
    real = critical.count
    monkeypatch.setattr(critical, "count", lambda *a, **k: {**real(*a, **k), "line_dropped": 1})
    dataset, recordings = _setup(tmp_path, CLEAN)
    baseline = tmp_path / "baseline.json"
    code = se.evaluate("replay", "test", dataset_dir=dataset, recordings_path=recordings, baseline_path=baseline,
                       set_baseline=True)  # fmt: skip
    assert code == 3
    assert "STOP:" in capsys.readouterr().out
    assert not baseline.exists()
    assert no_network == []


def test_set_baseline_meeting_the_target_writes_the_baseline(tmp_path, capsys, no_network):
    dataset, recordings = _setup(tmp_path, ("OS-001", "OS-011"))
    baseline = tmp_path / "baseline.json"
    code = se.evaluate("replay", "test", dataset_dir=dataset, recordings_path=recordings, baseline_path=baseline,
                       set_baseline=True)  # fmt: skip
    assert code == 0
    stored = json.loads(baseline.read_text(encoding="utf-8"))
    assert stored["scenarios"] == {"OS-001": True, "OS-011": True}
    assert "STOP:" not in capsys.readouterr().out
    assert no_network == []


def test_missing_recordings_exit_two(tmp_path, capsys, no_network):
    dataset, recordings = _setup(tmp_path, ("OS-001",))
    recordings.write_text("", encoding="utf-8")
    code = se.evaluate("replay", "test", dataset_dir=dataset, recordings_path=recordings,
                       baseline_path=tmp_path / "none.json")  # fmt: skip
    assert code == 2
    assert "missing recordings" in capsys.readouterr().err


def test_a_missing_recording_the_clarify_step_swallows_still_exits_two(tmp_path, capsys, no_network):
    # OS-026: the oxygen concentrator is out of the catalog, so the clarify step asks; its question has no
    # recording, and the clarify step turns that into a step failure instead of raising it.
    dataset, recordings = _setup(tmp_path, ("OS-026",))
    code = se.evaluate("replay", "test", dataset_dir=dataset, recordings_path=recordings,
                       baseline_path=tmp_path / "none.json")  # fmt: skip
    captured = capsys.readouterr()
    assert code == 2, captured.out
    assert "missing recordings" in captured.err


def _item(**expected) -> dict:
    lines = [
        {"sku": "SAL-500", "quantity": 12, "price_eur": 2.1},
        {"sku": "GLV-NIT-M", "quantity": 20, "price_eur": 6.5},
    ]
    return {"expected": {"orders": 1, "lines": lines, "clarification": None, "needs_review": 0, **expected}}


def _measured(**changes) -> dict:
    lines = [
        {"sku": "GLV-NIT-M", "quantity": 20, "price_eur": 6.5},
        {"sku": "SAL-500", "quantity": 12, "price_eur": 2.1},
    ]
    return {"orders": 1, "lines": lines, "clarification": None, "needs_review": 0, "confirmed": True, **changes}


NO_ERRORS = dict.fromkeys(critical.KINDS, 0)


def test_the_grader_reads_orders_lines_prices_clarification_review_and_the_confirming_reply():
    assert se.grade(_item(), _measured(), NO_ERRORS)["correct"]
    wrong_price = [{"sku": "GLV-NIT-M", "quantity": 20, "price_eur": 7.0}, _measured()["lines"][1]]
    cases = {
        "orders": _measured(orders=2),
        "lines": _measured(lines=wrong_price),
        "clarification": _measured(clarification="pending"),
        "needs_review": _measured(needs_review=1),
        "confirmed": _measured(confirmed=False),
    }
    for check, measured in cases.items():
        graded = se.grade(_item(), measured, NO_ERRORS)
        assert not graded["correct"] and [k for k, ok in graded["checks"].items() if not ok] == [check]
    for kind in critical.KINDS:
        graded = se.grade(_item(), _measured(), {**NO_ERRORS, kind: 1})
        assert not graded["correct"] and not graded["checks"]["critical"]


def test_the_mcnemar_gate_pairs_each_scenario_with_its_baseline():
    baseline = {"scenarios": {f"OS-{n:03d}": True for n in range(1, 11)}}
    six_lost = [{"id": f"OS-{n:03d}", "correct": n > 6} for n in range(1, 11)]
    failures, test = se.regression_gate(six_lost, baseline)
    assert test["lost"] == 6 and test["gained"] == 0 and test["p"] < 0.05
    assert failures and "McNemar" in failures[0]
    two_lost = [{"id": f"OS-{n:03d}", "correct": n > 2} for n in range(1, 11)]
    assert se.regression_gate(two_lost, baseline)[0] == []
    assert se.regression_gate(two_lost[:9], baseline)[0] == ["the evaluated scenarios differ from the baseline"]


def test_a_stored_baseline_is_read_and_gated(tmp_path, capsys, no_network):
    baseline = tmp_path / "baseline.json"
    scenarios = {"OS-001": True, "OS-011": True}
    baseline.write_text(json.dumps({"measured_at": "2026-10-09", "scenarios": scenarios}), encoding="utf-8")
    assert _evaluate(tmp_path, ("OS-001", "OS-011"), baseline=baseline) == 0
    assert "regression vs baseline 2026-10-09: no significant drop" in capsys.readouterr().out


def test_the_suite_is_in_all_after_its_baseline_and_stays_out_of_upload():
    assert "order_scenarios" in harness.SUITES
    assert "order_scenarios" in harness.ALL_SUITES
    assert "order_scenarios" not in harness.UPLOAD_SUITES
    assert harness._suite("order_scenarios") is se

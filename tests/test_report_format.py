"""C23: every report shows the target apart from the gate, the counting unit and the zero-event bound."""

from purchase_cycle.evaluation import clarification_eval, email_eval, harness, web_form_eval


def _row(out: str, metric: str) -> str:
    return next(line for line in out.splitlines() if line.startswith(metric + " "))


def _check(out: str, metric: str, unit: str, target: str, met: bool, gate: str) -> None:
    row = _row(out, metric)
    cells = row.split()
    assert unit in cells and target in cells and gate in cells, row
    assert ("not met" in row) is (not met), row
    assert met is False or " met " in row, row


def test_order_line_extraction_report(capsys):
    assert harness.evaluate("replay", "test") == 0
    out = capsys.readouterr().out
    assert "target met" in out and "gate" in out
    for metric in harness.METRICS:
        _check(out, metric, "line", ">=95.0%", True, "PASS")


def test_web_form_report(capsys):
    assert web_form_eval.evaluate("replay", "test") == 0
    out = capsys.readouterr().out
    assert "target met" in out
    _check(out, "line_product_accuracy", "line", ">=95.0%", True, "PASS")
    assert "submission none n/a" in " ".join(_row(out, "submission_accuracy").split())


def test_email_report(capsys):
    assert email_eval.evaluate("replay", "test") == 0
    out = capsys.readouterr().out
    assert "target met" in out
    _check(out, "intake_accuracy", "email", ">=95.0%", True, "PASS")
    _check(out, "line_recall", "expected_line", ">=95.0%", True, "PASS")
    _check(out, "line_precision", "produced_line", ">=95.0%", True, "PASS")
    assert "order_email none n/a" in " ".join(_row(out, "email_exact_match").split())


def test_detection_report_shows_the_target_not_met_and_the_zero_event_bound(capsys):
    assert clarification_eval.evaluate_detection("replay", "test") == 0
    out = capsys.readouterr().out
    assert "target met" in out
    for metric, gate in (
        ("recall_ambiguous", ">=89.1%"),
        ("recall_unknown", ">=94.3%"),
        ("recall_quantity", ">=91.3%"),
    ):
        _check(out, metric, "expected_doubt", ">=95.0%", False, "PASS")
        assert gate in _row(out, metric).split()
    _check(out, "false_question_rate", "clear_order", "<=5.0%", True, "PASS")
    assert _row(out, "false_question_rate").endswith("0 of 48, Wilson upper bound 7.4%")
    assert "raised_doubt none n/a" in " ".join(_row(out, "precision_unknown").split())


def test_answers_report(capsys):
    assert clarification_eval.evaluate_answers("replay", "test") == 0
    out = capsys.readouterr().out
    assert "target met" in out
    _check(out, "resolution_accuracy", "doubtful_line", ">=95.0%", True, "PASS")
    assert "case none n/a" in " ".join(_row(out, "case_exact_match").split())

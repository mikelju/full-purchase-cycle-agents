"""C22 (phase 05, deviation 05.1): the critical-error counter shared by `failure_recovery` and `order_scenarios`.

Each test injects one critical error into the final state of a real scenario run and proves the suite exits 1.
"""

import json
import re

import pytest

from purchase_cycle import db
from purchase_cycle.evaluation import critical, recovery_eval


def _sql(evidence, statement):
    conn = db.connect(evidence["db_path"])
    try:
        with conn:
            conn.execute(statement)
    finally:
        conn.close()


def _first_thread(evidence):
    return next(t for t, v in evidence["threads"].items() if v.get("order_id") is not None)


def _duplicate_order(evidence):
    _sql(
        evidence,
        "INSERT INTO orders (customer_code, channel, status) SELECT customer_code, channel, status FROM orders",
    )


def _price_not_catalog(evidence):
    reply = evidence["replies"][-1]
    reply["text"] = re.sub(r" at \d+\.\d+ EUR", " at 0.01 EUR", reply["text"], count=1)


def _line_dropped(evidence):
    _sql(evidence, "DELETE FROM order_lines")


def _open_doubt(evidence):
    thread = _first_thread(evidence)
    _sql(
        evidence,
        "INSERT INTO clarifications (thread_id, channel, customer_code, question, round, status) "
        f"SELECT '{thread}', channel, customer_code, 'Which size?', 1, 'pending' FROM orders",
    )
    # The pending row would also count as "asked about", so only the open doubt is injected here.


def _false_confirmation(evidence):
    reply = evidence["replies"][-1]
    reply["text"] = re.sub(r"registered as order \d+", "registered as order 7", reply["text"])


def _source_outside_message(evidence):
    values = evidence["threads"][_first_thread(evidence)]
    values["lines"] = [{**values["lines"][0], "source_text": "100 boxes of syringes 5 ml"}]


INJECTIONS = {
    "duplicate_order": _duplicate_order,
    "price_not_catalog": _price_not_catalog,
    "line_dropped": _line_dropped,
    "open_doubt": _open_doubt,
    "false_confirmation": _false_confirmation,
    "source_outside_message": _source_outside_message,
}


def test_every_kind_has_an_injection():
    assert tuple(INJECTIONS) == critical.KINDS


def _one_scenario(tmp_path, category="transient_then_success", channel="whatsapp"):
    item = next(i for i in recovery_eval.load_dataset() if i["category"] == category and i["channel"] == channel)
    path = tmp_path / "dataset.jsonl"
    path.write_text(json.dumps(item) + "\n", encoding="utf-8")
    return item, path


def test_a_clean_recovery_scenario_has_no_critical_error(tmp_path, capsys, no_network):
    _, path = _one_scenario(tmp_path)
    assert recovery_eval.evaluate("replay", "test", dataset_path=path) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("critical_errors "))
    assert row.split()[1] == "0" and row.endswith("PASS")
    assert "by kind: duplicate_order=0" in out
    assert no_network == []


@pytest.mark.parametrize("kind", critical.KINDS)
@pytest.mark.parametrize("channel", ["whatsapp", "email", "web_form"])
def test_each_injected_critical_error_fails_failure_recovery_with_exit_one(
    kind, channel, tmp_path, capsys, monkeypatch, no_network
):
    item, path = _one_scenario(tmp_path, channel=channel)
    real = recovery_eval._evidence

    def injected(*args, **kwargs):
        evidence = real(*args, **kwargs)
        INJECTIONS[kind](evidence)
        return evidence

    monkeypatch.setattr(recovery_eval, "_evidence", injected)
    assert recovery_eval.evaluate("replay", "test", dataset_path=path) == 1
    out = capsys.readouterr().out
    assert f"{kind}=1" in out
    assert f"  {item['id']}  critical errors {{'{kind}': 1}}" in out
    assert "GATE FAILED: critical_errors 1 above threshold 0" in out
    assert no_network == []


@pytest.mark.parametrize(
    ("question", "status", "own", "dropped"),
    [
        ("Which size of syringes 5 ml?", "pending", True, 1),
        (None, "pending", True, 0),
        (None, "pending", False, 1),
        (None, "answered", True, 1),
    ],
)
def test_a_dropped_line_counts_as_asked_about_only_when_an_open_question_names_it(
    question, status, own, dropped, tmp_path, monkeypatch, no_network
):
    # Task 1 limit tightened in task 3: any clarification row used to excuse every dropped line.
    # Review round 4: an answered question no longer excuses the line, since the answer settled it.
    # Review round 5: a pending question of a thread outside the scenario no longer excuses it either.
    item, path = _one_scenario(tmp_path)
    requested_text = recovery_eval.REQUESTED[item["channel"]][0][0]["text"]
    seen = {}
    real = recovery_eval._evidence

    def injected(*args, **kwargs):
        evidence = real(*args, **kwargs)
        _line_dropped(evidence)
        _sql(
            evidence,
            "INSERT INTO clarifications (thread_id, channel, customer_code, question, round, status) "
            f"SELECT '{_first_thread(evidence) if own else 'other'}', channel, customer_code, '{question or 'How many ' + requested_text + '?'}', 1, "
            f"'{status}' FROM orders",
        )
        seen.update(critical.count(**evidence))
        return evidence

    monkeypatch.setattr(recovery_eval, "_evidence", injected)
    recovery_eval.evaluate("replay", "test", dataset_path=path)
    assert seen["line_dropped"] == dropped
    assert no_network == []


@pytest.mark.parametrize("channel", ["whatsapp", "email", "web_form"])
def test_a_dropped_line_after_a_parked_and_resumed_thread_fails_failure_recovery(
    channel, tmp_path, capsys, monkeypatch, no_network
):
    # Review round 4: a resolved `failures` row used to excuse every dropped line of every scenario.
    item, path = _one_scenario(tmp_path, category="parked_then_resumed", channel=channel)
    seen = {}
    real = recovery_eval._evidence

    def injected(*args, **kwargs):
        evidence = real(*args, **kwargs)
        _line_dropped(evidence)
        seen.update(critical.count(**evidence))
        return evidence

    monkeypatch.setattr(recovery_eval, "_evidence", injected)
    assert recovery_eval.evaluate("replay", "test", dataset_path=path) == 1
    assert seen["line_dropped"] == 1
    assert "GATE FAILED: critical_errors 1 above threshold 0" in capsys.readouterr().out
    assert no_network == []


def test_only_an_unresolved_failure_of_the_scenario_threads_counts_as_escalated(tmp_path, monkeypatch, no_network):
    item, path = _one_scenario(tmp_path)
    seen = []
    real = recovery_eval._evidence

    def injected(*args, **kwargs):
        evidence = real(*args, **kwargs)
        _line_dropped(evidence)
        thread = _first_thread(evidence)
        for owner, status in (("other", "needs_review"), (thread, "resolved"), (thread, "needs_review")):
            _sql(
                evidence,
                "INSERT INTO failures (thread_id, channel, source, step, error, status) "
                f"VALUES ('{owner}', 'whatsapp', 'x', 'store', 'e', '{status}') "
                "ON CONFLICT (thread_id) DO UPDATE SET status = excluded.status",
            )
            seen.append(critical.count(**evidence)["line_dropped"])
        return evidence

    monkeypatch.setattr(recovery_eval, "_evidence", injected)
    recovery_eval.evaluate("replay", "test", dataset_path=path)
    assert seen == [1, 1, 0]
    assert no_network == []

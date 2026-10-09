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

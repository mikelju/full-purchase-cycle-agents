"""C2 to C6 and C8: web form validation, matching, storing, reply and resumption."""

import json
import subprocess
import sys
import textwrap

import pytest

from purchase_cycle import db
from purchase_cycle.llm import MATCHING, ModelClient
from purchase_cycle.web_form import MAX_LINES, build_web_form_graph, catalog_index, match_key

ALCOHOL = "alcohol 70 250ml"
BED = "hospital bed"
SUBMISSION = {
    "submission_id": "WF-TEST-1",
    "customer_code": "CLI-002",
    "lines": [
        {"product": "GLV-NIT-M", "quantity": 40},
        {"product": ALCOHOL, "quantity": 12},
        {"product": BED, "quantity": 1},
    ],
}
EXPECTED_REPLY = """Dear Iker Zubiri,

Thank you. Your web form order WF-TEST-1 is registered as order 1:
- Nitrile examination gloves, powder-free, size M: 40 x box of 100 at 6.90 EUR = 276.00 EUR
- Ethyl alcohol 70% 250 ml: 12 x bottle at 1.52 EUR = 18.24 EUR
Order total: 294.24 EUR

We could not find these products in our catalog, so they are not part of the order:
- "hospital bed" (1)

Kind regards,
Customer service"""


@pytest.fixture
def recordings(seeded_db, write_recording):
    _, catalog = seeded_db
    write_recording(catalog, ALCOHOL, {"sku": "ALC70-250"}, task=MATCHING)
    return write_recording(catalog, BED, {"sku": None}, task=MATCHING)


def _graph(seeded_db, recordings_path, **kwargs):
    db_path, catalog = seeded_db
    client = ModelClient("replay", catalog, recordings_path, task=MATCHING)
    return client, build_web_form_graph(client, db_path, **kwargs)


def _rows(db_path):
    conn = db.connect(db_path)
    try:
        orders = [dict(r) for r in conn.execute("SELECT customer_code, channel, status FROM orders")]
        lines = [dict(r) for r in conn.execute("SELECT order_id, sku, quantity FROM order_lines ORDER BY id")]
    finally:
        conn.close()
    return orders, lines


def _submission(**changes):
    return {**SUBMISSION, **changes}


@pytest.mark.parametrize(
    ("submission", "field"),
    [
        (_submission(customer_code="CLI-999"), "customer_code"),
        (_submission(lines=[]), "lines"),
        (_submission(lines=[{"product": "GLV-NIT-M", "quantity": 1}] * (MAX_LINES + 1)), "lines"),
        (_submission(lines=[{"product": "   ", "quantity": 1}]), "lines.0.product"),
        (_submission(lines=[{"product": "GLV-NIT-M", "quantity": 0}]), "lines.0.quantity"),
        (_submission(lines=[{"product": "GLV-NIT-M", "quantity": -2}]), "lines.0.quantity"),
        (_submission(lines=[{"product": "GLV-NIT-M", "quantity": 2.5}]), "lines.0.quantity"),
        (_submission(lines=[{"product": "GLV-NIT-M", "quantity": "3"}]), "lines.0.quantity"),
    ],
)
def test_invalid_submission_is_rejected_without_model_or_rows(seeded_db, recordings, submission, field):
    client, graph = _graph(seeded_db, recordings)
    state = graph.invoke({"submission": submission})
    assert any(f"field '{field}'" in error for error in state["errors"]), state["errors"]
    assert "reply" not in state
    assert client.calls == 0
    assert _rows(seeded_db[0]) == ([], [])


@pytest.mark.parametrize(
    ("text", "sku"),
    [
        ("GLV-NIT-M", "GLV-NIT-M"),
        ("glv nit m", "GLV-NIT-M"),
        ("  Nitrile EXAMINATION gloves powder free   size m. ", "GLV-NIT-M"),
        ("Ethyl alcohol 70% 250 ml", "ALC70-250"),
        ("glv-surg-ltx-6.5", "GLV-SURG-LTX-6.5"),
    ],
)
def test_catalog_sku_or_name_is_matched_without_model(seeded_db, tmp_path, text, sku):
    client, graph = _graph(seeded_db, tmp_path / "none.jsonl")
    state = graph.invoke({"submission": _submission(lines=[{"product": text, "quantity": 3}])})
    assert state["lines"] == [{"product": text.strip(), "quantity": 3, "sku": sku, "source": "deterministic"}]
    assert client.calls == 0


def test_catalog_keys_are_unique(seeded_db):
    _, catalog = seeded_db
    assert len(catalog_index(catalog)) == 2 * len(catalog)
    assert match_key("  Saline   solution 0.9%! ") == "saline solution 0 9"


def test_other_lines_go_to_the_model(seeded_db, recordings):
    client, graph = _graph(seeded_db, recordings)
    state = graph.invoke({"submission": SUBMISSION})
    assert [(line["sku"], line["source"]) for line in state["lines"]] == [
        ("GLV-NIT-M", "deterministic"),
        ("ALC70-250", "model"),
        (None, "model"),
    ]
    assert client.calls == 2


def test_valid_submission_stores_one_order(seeded_db, recordings):
    _, graph = _graph(seeded_db, recordings)
    state = graph.invoke({"submission": SUBMISSION})
    orders, lines = _rows(seeded_db[0])
    assert orders == [{"customer_code": "CLI-002", "channel": "web_form", "status": "received"}]
    assert lines == [
        {"order_id": state["order_id"], "sku": "GLV-NIT-M", "quantity": 40},
        {"order_id": state["order_id"], "sku": "ALC70-250", "quantity": 12},
    ]


def test_submission_without_matched_lines_stores_nothing(seeded_db, recordings):
    _, graph = _graph(seeded_db, recordings)
    state = graph.invoke({"submission": _submission(lines=[{"product": BED, "quantity": 2}])})
    assert state["order_id"] is None
    assert _rows(seeded_db[0]) == ([], [])
    assert "We could not register your web form order WF-TEST-1." in state["reply"]
    assert '- "hospital bed" (2)' in state["reply"]


def test_store_is_one_transaction(seeded_db, recordings):
    # A line the database refuses rolls back the order row written before it.
    _, graph = _graph(seeded_db, recordings)
    conn = db.connect(seeded_db[0])
    conn.execute(
        "CREATE TRIGGER refuse BEFORE INSERT ON order_lines WHEN NEW.sku = 'ALC70-250' BEGIN SELECT RAISE(ABORT, 'refused'); END"
    )
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="refused"):
        graph.invoke({"submission": SUBMISSION})
    assert _rows(seeded_db[0]) == ([], [])


def test_reply_uses_stored_rows_and_database_prices(seeded_db, recordings):
    _, graph = _graph(seeded_db, recordings)
    assert graph.invoke({"submission": SUBMISSION})["reply"] == EXPECTED_REPLY


RUN_STEP = textwrap.dedent(
    """
    import json, sys
    from purchase_cycle import db
    from purchase_cycle.graph import sqlite_checkpointer
    from purchase_cycle.llm import MATCHING, ModelClient
    from purchase_cycle.web_form import build_web_form_graph

    db_path, recordings, checkpoints, phase, submission = sys.argv[1:6]
    conn = db.connect(db_path)
    client = ModelClient("replay", db.catalog_rows(conn), recordings, task=MATCHING)
    conn.close()
    run = {"configurable": {"thread_id": "web-form-resume"}}
    if phase == "first":
        graph = build_web_form_graph(client, db_path, sqlite_checkpointer(checkpoints), interrupt_after=["match"])
        graph.invoke({"submission": json.loads(submission)}, run)
    else:
        graph = build_web_form_graph(client, db_path, sqlite_checkpointer(checkpoints))
        graph.invoke(None, run)
    conn = db.connect(db_path)
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    print(json.dumps({"calls": client.calls, "next": list(graph.get_state(run).next), "orders": orders}))
    """
)


def _step(*args):
    out = subprocess.run([sys.executable, "-c", RUN_STEP, *map(str, args)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_resume_after_match_stores_once_without_model(seeded_db, recordings, tmp_path):
    args = (seeded_db[0], recordings, tmp_path / "checkpoints.db")
    first = _step(*args, "first", json.dumps(SUBMISSION))
    assert (first["calls"], first["next"], first["orders"]) == (2, ["store"], 0)
    second = _step(*args, "second", "")
    assert (second["calls"], second["next"], second["orders"]) == (0, [], 1)


def test_demo_command_prints_matching_order_and_reply(tmp_path, no_network, capsys):
    from purchase_cycle.cli import main

    code = main(["--db", str(tmp_path / "b.db"), "web-form-demo", "--checkpoints", str(tmp_path / "c.db")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert '1. "GLV-NIT-M" x 40 -> GLV-NIT-M  (deterministic)' in out
    assert '3. "cotton rounds" x 6 -> COT-PADS  (model)' in out
    assert '6. "compressor nebuliser machine" x 1 -> no match  (model)' in out
    assert "stored order: 1" in out
    assert "Order total: 379.94 EUR" in out
    assert no_network == []


def test_demo_command_reports_a_rejected_submission(tmp_path, capsys):
    from purchase_cycle.cli import main

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_submission(customer_code="CLI-999")), encoding="utf-8")
    code = main(["--db", str(tmp_path / "b.db"), "web-form-demo", str(bad), "--checkpoints", str(tmp_path / "c.db")])
    assert code == 1
    assert "field 'customer_code': unknown customer code 'CLI-999'" in capsys.readouterr().out

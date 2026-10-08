"""C2, C4, C5, C6, C7 and C8 (phase 03): the email order subgraph with recorded answers and its demo command."""

import pytest

from conftest import make_email
from purchase_cycle import db
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.email_order import (
    MAX_EMAIL_TEXT,
    InvalidExtraction,
    build_email_order_graph,
    model_text,
    parse_email,
)
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import EMAIL_EXTRACTION, EMAIL_INTAKE, InvalidModelOutput, ModelClient

SENDER = CUSTOMERS[1].email  # CLI-002, Iker Zubiri
SUBJECT = "Order for next week"
BODY = "Hello,\n\nPlease send 40 boxes of nitrile gloves M.\n\nThanks,\nIker"
EXTRA = ("extra.txt", b"12 x alcohol 70% 250 ml\n2 hospital beds\n", "text/plain")
ORDER = {"is_order": True, "reason": "The customer orders gloves, alcohol and beds."}
LINES = [
    {"source": "body", "source_text": "40 boxes of nitrile gloves M", "sku": "GLV-NIT-M", "quantity": 40},
    {"source": "extra.txt", "source_text": "12 x alcohol 70% 250 ml", "sku": "ALC70-250", "quantity": 12},
    {"source": "extra.txt", "source_text": "2 hospital beds", "sku": None, "quantity": 2},
]
EXPECTED_REPLY = f"""To: {SENDER}
Subject: Re: {SUBJECT}

Dear Iker Zubiri,

Thank you. Your email order "{SUBJECT}" is registered as order 1:
- Nitrile examination gloves, powder-free, size M: 40 x box of 100 at 6.90 EUR = 276.00 EUR
- Ethyl alcohol 70% 250 ml: 12 x bottle at 1.52 EUR = 18.24 EUR
Order total: 294.24 EUR

We could not find these products in our catalog, so they are not part of the order:
- "2 hospital beds" (2)

Kind regards,
Customer service"""


@pytest.fixture
def email_path(tmp_path):
    return make_email(tmp_path / "EML-TEST-1.eml", SENDER, SUBJECT, BODY, attachments=[EXTRA])


@pytest.fixture
def run(seeded_db, write_recording, email_path):
    """Record the intake and extractor answers for an email, run the subgraph and return state and clients."""
    db_path, catalog = seeded_db

    def _run(intake_answer=ORDER, lines_answer=None, path=email_path, **graph_kwargs):
        text = model_text(parse_email(path.read_bytes()))
        recordings = write_recording(catalog, text, intake_answer, task=EMAIL_INTAKE)
        lines_answer = {"lines": LINES} if lines_answer is None else lines_answer
        recordings = write_recording(catalog, text, lines_answer, task=EMAIL_EXTRACTION)
        intake = ModelClient("replay", catalog, recordings, task=EMAIL_INTAKE)
        extraction = ModelClient("replay", catalog, recordings, task=EMAIL_EXTRACTION)
        graph = build_email_order_graph(intake, extraction, db_path, **graph_kwargs)
        config = {"configurable": {"thread_id": path.stem}}
        return graph, graph.invoke({"email_path": str(path)}, config), intake, extraction

    return _run


def _rows(db_path):
    conn = db.connect(db_path)
    try:
        orders = [dict(r) for r in conn.execute("SELECT id, customer_code, channel, status FROM orders")]
        lines = [dict(r) for r in conn.execute("SELECT order_id, sku, quantity FROM order_lines ORDER BY id")]
    finally:
        conn.close()
    return orders, lines


def test_order_email_stores_one_order_with_matched_lines(seeded_db, run):
    _, state, intake, extraction = run()
    assert (state["errors"], state["is_order"], state["order_id"]) == ([], True, 1)
    assert state["lines"] == LINES
    assert (intake.calls, extraction.calls) == (1, 1)
    orders, lines = _rows(seeded_db[0])
    assert orders == [{"id": 1, "customer_code": "CLI-002", "channel": "email", "status": "received"}]
    assert lines == [
        {"order_id": 1, "sku": "GLV-NIT-M", "quantity": 40},
        {"order_id": 1, "sku": "ALC70-250", "quantity": 12},
    ]


def test_reply_is_addressed_to_the_sender_and_quotes_the_subject(run):
    assert run()[1]["reply"] == EXPECTED_REPLY


def test_email_without_matched_lines_stores_no_order(seeded_db, run):
    unmatched = [{"source": "extra.txt", "source_text": "2 hospital beds", "sku": None, "quantity": 2}]
    _, state, _, _ = run(lines_answer={"lines": unmatched})
    assert state["order_id"] is None
    assert _rows(seeded_db[0]) == ([], [])
    assert f'We could not register your email order "{SUBJECT}".' in state["reply"]
    assert '- "2 hospital beds" (2)' in state["reply"]


def test_not_an_order_stores_nothing_and_skips_the_extractor(seeded_db, run):
    decision = {"is_order": False, "reason": "The customer asks for a quote."}
    _, state, intake, extraction = run(intake_answer=decision)
    assert (state["is_order"], state["reason"]) == (False, "The customer asks for a quote.")
    assert (intake.calls, extraction.calls) == (1, 0)
    assert "lines" not in state and "reply" not in state
    assert _rows(seeded_db[0]) == ([], [])


def _line(**changes):
    return {"lines": [LINES[0], {**LINES[1], **changes}]}


@pytest.mark.parametrize(
    ("answer", "error", "message"),
    [
        (_line(sku="ALC70-999"), InvalidExtraction, "SKU 'ALC70-999' is not in the catalog"),
        (_line(quantity=0), InvalidModelOutput, "lines.1.quantity"),
        (_line(quantity=-2), InvalidModelOutput, "lines.1.quantity"),
        ({"lines": [{"source": "body", "sku": "GLV-NIT-M", "quantity": 40}]}, InvalidModelOutput, "source_text"),
    ],
)
def test_invalid_extraction_stops_before_any_write(seeded_db, run, answer, error, message):
    with pytest.raises(error, match=message):
        run(lines_answer=answer)
    assert _rows(seeded_db[0]) == ([], [])


@pytest.mark.parametrize(
    ("source", "stored"),
    [(" EXTRA.TXT ", "extra.txt"), ("order.pdf", "order.pdf")],
    ids=["case-and-spaces", "unmatched-kept"],
)
def test_line_source_is_matched_loosely_and_never_stops_the_email(seeded_db, run, source, stored):
    _, state, _, _ = run(lines_answer=_line(source=source))
    assert state["lines"][1]["source"] == stored
    assert state["order_id"] == 1


def test_huge_quantity_fails_the_email_without_a_partial_write(seeded_db, run):
    with pytest.raises(OverflowError):
        run(lines_answer=_line(quantity=10**30))
    assert _rows(seeded_db[0]) == ([], [])


def test_reply_does_not_repeat_re(tmp_path, run):
    path = make_email(tmp_path / "EML-RE.eml", SENDER, "RE: weekly order", BODY, attachments=[EXTRA])
    assert run(path=path)[1]["reply"].startswith(f"To: {SENDER}\nSubject: RE: weekly order\n\n")


def test_store_is_one_transaction(seeded_db, run):
    conn = db.connect(seeded_db[0])
    conn.execute(
        "CREATE TRIGGER refuse BEFORE INSERT ON order_lines WHEN NEW.sku = 'ALC70-250' BEGIN SELECT RAISE(ABORT, 'refused'); END"
    )
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="refused"):
        run()
    assert _rows(seeded_db[0]) == ([], [])


@pytest.mark.parametrize(
    ("sender", "content", "reason"),
    [
        ("stranger@example.org", None, "the sender 'stranger@example.org' is not a known customer"),
        (None, b"Subject: Order\r\n\r\n40 gloves", "the email cannot be parsed: no sender address"),
        (SENDER, "gloves " * (MAX_EMAIL_TEXT // 7 + 1), f"above the limit of {MAX_EMAIL_TEXT}"),
        (
            None,
            f"From: {SENDER}\r\nSubject: Order\r\nContent-Type: multipart/mixed\r\n\r\n40 gloves".encode(),
            "the email cannot be parsed: broken MIME structure (NoBoundaryInMultipartDefect",
        ),
    ],
    ids=["unknown-sender", "unparseable", "over-long", "no-boundary"],
)
def test_rejected_email_makes_no_model_call_and_writes_nothing(seeded_db, tmp_path, sender, content, reason):
    db_path, catalog = seeded_db
    path = tmp_path / "EML-BAD.eml"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        make_email(path, sender, SUBJECT, content or BODY)
    intake = ModelClient("replay", catalog, tmp_path / "none.jsonl", task=EMAIL_INTAKE)
    extraction = ModelClient("replay", catalog, tmp_path / "none.jsonl", task=EMAIL_EXTRACTION)
    state = build_email_order_graph(intake, extraction, db_path).invoke({"email_path": str(path)})
    assert len(state["errors"]) == 1 and reason in state["errors"][0]
    assert (intake.calls, extraction.calls) == (0, 0)
    assert _rows(db_path) == ([], [])


def test_run_is_checkpointed_per_email_thread(tmp_path, run, email_path):
    checkpointer = sqlite_checkpointer(tmp_path / "checkpoints.db")
    graph, state, _, _ = run(checkpointer=checkpointer)
    saved = graph.get_state({"configurable": {"thread_id": email_path.stem}})
    assert saved.values["reply"] == state["reply"]
    assert saved.next == ()


def test_email_demo_command_prints_intake_lines_order_and_reply(tmp_path, no_network, capsys):
    from purchase_cycle.cli import main

    code = main(["--db", str(tmp_path / "b.db"), "email-demo", "--checkpoints", str(tmp_path / "c.db")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "emails=4" in out
    assert "intake: not an order - " in out
    assert "nothing extracted or stored" in out
    assert out.count("intake: order - ") == 3
    assert '2. "Adjustable lumbar support belt 16 unit" x 16 -> SUP-LUMBAR  (delivery-note-0001.pdf)' in out
    assert '3. "Saline 0.9% irrigation, 500 ml bottles | 5 | bottle" x 5 -> SAL-500  (order-0012.xlsx)' in out
    assert '1. "13 boxes of surgical gloves in size 9" x 13 -> no match  (body)' in out
    assert '2. "eighteen non-contact infrared forehead thermometers" x 18 -> THERM-IR  (body)' in out
    assert [line for line in out.splitlines() if line.startswith("stored order:")] == [
        "stored order: 1",
        "stored order: 2",
        "stored order: 3",
    ]
    assert "Subject: Re: Second order this month - nursing wing" in out
    assert "Order total: 1417.80 EUR" in out
    assert 'Your email order "Larger glove sizes and thermometers" is registered as order 3:' in out
    assert '- "13 boxes of surgical gloves in size 9" (13)' in out
    assert no_network == []


def test_email_demo_command_names_a_failed_email_and_goes_on(tmp_path, no_network, capsys, monkeypatch):
    from purchase_cycle import cli

    class Failing:
        def invoke(self, state, config):
            raise OverflowError("Python int too large to convert to SQLite INTEGER")

    monkeypatch.setattr(cli, "build_email_order_graph", lambda *args, **kwargs: Failing())
    code = cli.main(["--db", str(tmp_path / "b.db"), "email-demo", "--checkpoints", str(tmp_path / "c.db")])
    err = capsys.readouterr().err
    assert code == 1
    assert err.count("failed: OverflowError: Python int too large") == 4


def test_email_demo_command_reports_an_empty_folder(tmp_path, capsys):
    from purchase_cycle.cli import main

    assert (
        main(["--db", str(tmp_path / "b.db"), "email-demo", str(tmp_path), "--checkpoints", str(tmp_path / "c.db")])
        == 1
    )
    assert "no .eml files" in capsys.readouterr().err

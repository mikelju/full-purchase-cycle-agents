"""C2 and C3 (phase 05): the simulated WhatsApp channel and its order graph."""

import json

import pytest

from purchase_cycle import db
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.whatsapp_order import (
    TEXT_ONLY_REPLY,
    WhatsAppRejected,
    phone_digits,
    read_whatsapp,
    write_outbox,
)

CUSTOMER = CUSTOMERS[1]  # CLI-002, Iker Zubiri, +34 600 101 201
PHONE = "34600101201"
BODY = "hi! can u send 40 boxes of nitrile gloves M thx"


def message(message_id="wamid.TEST1", sender=PHONE, kind="text", body=BODY, **extra) -> dict:
    out = {"message_id": message_id, "from": sender, "timestamp": "1760000000", "type": kind}
    if kind == "text":
        out["text"] = {"body": body}
    return {**out, **extra}


@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()

    def _write(content, name="WA-TEST-1.json") -> str:
        path = folder / name
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return str(path)

    return _write


@pytest.fixture
def conn(seeded_db):
    conn = db.connect(seeded_db[0])
    yield conn
    conn.close()


def _orders(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def test_phone_lookup_keeps_digits_only():
    assert phone_digits(CUSTOMER.phone) == PHONE
    assert phone_digits("+34 600-101-201") == PHONE


@pytest.mark.parametrize("sender", [PHONE, "+34 600 101 201", "+34-600-101-201"])
def test_known_number_identifies_the_customer(conn, inbox, sender):
    read = read_whatsapp(inbox(message(sender=sender)), conn)
    assert read["customer"]["code"] == CUSTOMER.code
    assert (read["message_id"], read["from"], read["body"]) == ("wamid.TEST1", sender, BODY)


def test_unknown_number_is_rejected_with_no_reply(conn, inbox):
    with pytest.raises(WhatsAppRejected, match="'34699999999' is not a known customer") as raised:
        read_whatsapp(inbox(message(sender="34699999999")), conn)
    assert raised.value.reply_to is None
    assert _orders(conn) == 0


@pytest.mark.parametrize(
    "content, reason",
    [
        ("{not json", "is not valid JSON"),
        ("[1, 2]", "is not a WhatsApp message"),
        ({"from": PHONE, "timestamp": "1", "type": "text", "text": {"body": "x"}}, "field 'message_id'"),
        (message(message_id="../../x"), "field 'message_id'"),
        (message(sender="no digits"), "field 'from'"),
        (message(text={"body": ""}), "field 'text.body'"),
        ({k: v for k, v in message().items() if k != "text"}, "a text message needs text.body"),
    ],
)
def test_malformed_file_is_rejected_with_a_reason(conn, inbox, content, reason):
    with pytest.raises(WhatsAppRejected, match="malformed") as raised:
        read_whatsapp(inbox(content), conn)
    assert reason in str(raised.value)
    assert raised.value.reply_to is None
    assert _orders(conn) == 0


def test_unreadable_file_is_rejected(conn, tmp_path):
    with pytest.raises(WhatsAppRejected, match="cannot be read"):
        read_whatsapp(tmp_path / "missing.json", conn)


@pytest.mark.parametrize("kind", ["image", "audio", "document"])
def test_non_text_message_from_known_customer_gets_the_text_only_reply(conn, inbox, tmp_path, kind):
    path = inbox(message(kind=kind, **{kind: {"id": "media-1"}}))
    with pytest.raises(WhatsAppRejected, match=f"type '{kind}' is not text") as raised:
        read_whatsapp(path, conn)
    rejected = raised.value
    assert (rejected.reply_to["to"], rejected.reply_to["message_id"]) == (PHONE, "wamid.TEST1")
    assert rejected.reply_to["customer"]["code"] == CUSTOMER.code
    written = write_outbox(tmp_path / "outbox", PHONE, "wamid.TEST1", TEXT_ONLY_REPLY)
    assert json.loads(written.read_text(encoding="utf-8")) == {
        "in_reply_to": "wamid.TEST1",
        "text": TEXT_ONLY_REPLY,
        "to": PHONE,
    }
    assert "as text" in TEXT_ONLY_REPLY
    assert _orders(conn) == 0


def test_non_text_message_from_unknown_number_gets_no_reply(conn, inbox):
    with pytest.raises(WhatsAppRejected, match="not a known customer") as raised:
        read_whatsapp(inbox(message(sender="34699999999", kind="image", image={"id": "m"})), conn)
    assert raised.value.reply_to is None


def test_outbox_file_is_keyed_by_the_answered_message(tmp_path):
    outbox = tmp_path / "outbox"
    first = write_outbox(outbox, PHONE, "wamid.TEST1", "Hello")
    again = write_outbox(outbox, PHONE, "wamid.TEST1", "Hello")
    assert first == again == outbox / "reply-wamid.TEST1.json"
    assert [p.name for p in outbox.iterdir()] == ["reply-wamid.TEST1.json"]


# C3: the WhatsApp graph in replay with hand-written recordings.
ORDER = {"is_order": True, "reason": "The customer orders gloves and a bed."}
LINES = [
    {"source": "message", "source_text": "40 boxes of nitrile gloves M", "sku": "GLV-NIT-M", "quantity": 40},
    {"source": "message", "source_text": "a hospital bed", "sku": None, "quantity": 1},
]


@pytest.fixture
def run(seeded_db, write_recording, inbox, tmp_path):
    from purchase_cycle.llm import WHATSAPP_EXTRACTION, WHATSAPP_INTAKE, ModelClient
    from purchase_cycle.whatsapp_order import build_whatsapp_order_graph, model_text

    db_path, catalog = seeded_db

    def _run(intake_answer=ORDER, lines=LINES, content=None):
        path = inbox(content or message())
        text = model_text({"body": BODY})
        recordings = write_recording(catalog, text, intake_answer, task=WHATSAPP_INTAKE)
        write_recording(catalog, text, {"lines": lines}, task=WHATSAPP_EXTRACTION)
        intake = ModelClient("replay", catalog, recordings, task=WHATSAPP_INTAKE)
        extraction = ModelClient("replay", catalog, recordings, task=WHATSAPP_EXTRACTION)
        graph = build_whatsapp_order_graph(intake, extraction, db_path, tmp_path / "outbox")
        state = graph.invoke({"message_path": path}, {"configurable": {"thread_id": "whatsapp-1"}})
        return state, intake, extraction

    return _run


def _outbox(tmp_path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((tmp_path / "outbox").glob("*.json"))]


def test_clear_order_stores_one_whatsapp_order_and_writes_the_reply(seeded_db, run, tmp_path):
    state, intake, extraction = run()
    conn = db.connect(seeded_db[0])
    orders = [dict(r) for r in conn.execute("SELECT id, customer_code, channel FROM orders")]
    lines = [tuple(r) for r in conn.execute("SELECT sku, quantity FROM order_lines")]
    conn.close()
    assert orders == [{"id": 1, "customer_code": CUSTOMER.code, "channel": "whatsapp"}]
    assert lines == [("GLV-NIT-M", 40)]
    assert (intake.calls, extraction.calls) == (1, 1)
    [sent] = _outbox(tmp_path)
    assert (sent["to"], sent["in_reply_to"]) == (PHONE, "wamid.TEST1")
    assert sent["text"] == state["reply"]
    assert "Your WhatsApp order is registered as order 1" in sent["text"]
    assert '"a hospital bed" (1)' in sent["text"]


def test_non_order_stores_nothing_and_gets_a_polite_reply(seeded_db, run, tmp_path):
    state, _, extraction = run(intake_answer={"is_order": False, "reason": "The customer says thanks."})
    conn = db.connect(seeded_db[0])
    assert _orders(conn) == 0
    conn.close()
    assert extraction.calls == 0
    [sent] = _outbox(tmp_path)
    assert sent["text"].startswith(f"Dear {CUSTOMER.contact_name},")
    assert "does not look like an order" in sent["text"]


@pytest.mark.parametrize("source", ["body", "attachment.txt", "chat", ""])
def test_only_message_is_a_valid_whatsapp_line_source(seeded_db, run, tmp_path, source):
    from purchase_cycle.email_order import InvalidExtraction
    from purchase_cycle.llm import InvalidModelOutput

    expected = InvalidModelOutput if source == "" else InvalidExtraction
    with pytest.raises(expected):
        run(lines=[{**LINES[0], "source": source}])
    conn = db.connect(seeded_db[0])
    assert _orders(conn) == 0
    conn.close()
    assert not (tmp_path / "outbox").exists()


def test_non_text_message_through_the_graph_writes_only_the_text_only_reply(seeded_db, run, tmp_path):
    state, intake, _ = run(content=message(kind="image", image={"id": "m"}))
    assert intake.calls == 0
    assert [s["text"] for s in _outbox(tmp_path)] == [TEXT_ONLY_REPLY]


def test_whatsapp_tasks_are_distinct_from_the_email_tasks():
    from purchase_cycle import llm

    whatsapp, email = (llm.WHATSAPP_INTAKE, llm.WHATSAPP_EXTRACTION), (llm.EMAIL_INTAKE, llm.EMAIL_EXTRACTION)
    for new, old in zip(whatsapp, email, strict=True):
        assert new.name != old.name and new.tool_name != old.tool_name
        assert new.instructions != old.instructions
        assert new.recordings_path != old.recordings_path
    assert [t.recordings_path.name for t in whatsapp] == ["whatsapp_intake.jsonl", "whatsapp_order_extraction.jsonl"]

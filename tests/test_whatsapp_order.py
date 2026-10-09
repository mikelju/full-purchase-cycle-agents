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

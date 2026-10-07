"""C2 and C3 (phase 03): email parsing, attachment text, ignored attachments and deterministic rejections."""

import pytest

from conftest import make_email, text_pdf, xlsx_bytes
from purchase_cycle import db
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.email_order import MAX_EMAIL_TEXT, EmailRejected, model_text, parse_email, read_email

SENDER = CUSTOMERS[1].email  # iker.cli-002@example.com


def _parse(path):
    return parse_email(path.read_bytes())


def test_plain_body(tmp_path):
    path = make_email(
        tmp_path / "a.eml", SENDER, "Weekly order", "Hello,\n\n  40 boxes   of nitrile gloves M\n\nThanks\n"
    )
    email = _parse(path)
    assert email["sender"] == SENDER
    assert email["subject"] == "Weekly order"
    assert email["body"] == "Hello,\n\n40 boxes of nitrile gloves M\n\nThanks"
    assert (email["attachments"], email["ignored"]) == ([], [])


def test_html_body_is_reduced_to_text(tmp_path):
    html = (
        "<html><head><style>p {color: red}</style></head><body><p>Hello,</p>"
        "<table><tr><td>Gloves M</td><td>40</td></tr><tr><td>Alcohol 70% 250 ml</td><td>12</td></tr></table>"
        "<p>Thanks&nbsp;a lot<br>Iker</p></body></html>"
    )
    email = _parse(make_email(tmp_path / "a.eml", SENDER, body=None, html=html))
    assert email["body"] == "Hello,\n\nGloves M\t40\nAlcohol 70% 250 ml\t12\n\nThanks a lot\nIker"


def test_plain_alternative_is_preferred_over_html(tmp_path):
    email = _parse(make_email(tmp_path / "a.eml", SENDER, body="Plain text", html="<p>HTML text</p>"))
    assert email["body"] == "Plain text"


def test_supported_attachments_become_text_and_others_are_ignored(tmp_path):
    attachments = [
        ("notes.txt", "2 bottles of alcohol\n".encode("latin-1"), "text/plain"),
        ("order.pdf", text_pdf([["Order form", "GLV-NIT-M 40"], ["ALC70-250 12"]]), "application/pdf"),
        (
            "order.xlsx",
            xlsx_bytes(
                {
                    "Order": [["Product", "Quantity", None], ["Gloves M", 40.0, None], [None, None], ["Alcohol", 12]],
                    "Notes": [["Not read", 1]],
                }
            ),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ("order.docx", b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ]
    email = _parse(make_email(tmp_path / "a.eml", SENDER, attachments=attachments))
    assert email["attachments"] == [
        {"name": "notes.txt", "text": "2 bottles of alcohol"},
        {"name": "order.pdf", "text": "Order form\nGLV-NIT-M 40\nALC70-250 12"},
        {"name": "order.xlsx", "text": "Product\tQuantity\nGloves M\t40\nAlcohol\t12"},
    ]
    assert email["ignored"] == ["photo.png", "order.docx"]


def test_txt_attachment_uses_its_charset(tmp_path):
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = SENDER
    message["Subject"] = "Order"
    message.set_content("See attachment")
    message.add_attachment("Guantes talla M: 40 cajas, 12 alcohol 70º", filename="pedido.txt", charset="latin-1")
    path = tmp_path / "a.eml"
    path.write_bytes(bytes(message))
    assert _parse(path)["attachments"] == [{"name": "pedido.txt", "text": "Guantes talla M: 40 cajas, 12 alcohol 70º"}]


def test_pdf_without_text_layer_is_ignored(tmp_path):
    email = _parse(
        make_email(tmp_path / "a.eml", SENDER, attachments=[("scan.pdf", text_pdf([[]]), "application/pdf")])
    )
    assert (email["attachments"], email["ignored"]) == ([], ["scan.pdf"])


def test_model_text_layout(tmp_path):
    attachments = [("notes.txt", b"2 bottles", "text/plain"), ("photo.png", b"x", "image/png")]
    email = _parse(make_email(tmp_path / "a.eml", SENDER, "Order", "Hello", attachments=attachments))
    assert model_text(email) == "Subject: Order\n\nBody:\nHello\n\nAttachment: notes.txt\n2 bottles"


@pytest.fixture
def business_db(tmp_path):
    conn = db.connect(tmp_path / "business.db")
    db.seed(conn)
    yield conn
    conn.close()


def test_sender_is_matched_ignoring_case(tmp_path, business_db):
    path = make_email(tmp_path / "a.eml", f"Iker Zubiri <{SENDER.upper()}>", "Order", "40 gloves M")
    email = read_email(path, business_db)
    assert email["customer"]["code"] == "CLI-002"
    assert email["sender"] == SENDER.upper()
    assert email["text"] == "Subject: Order\n\nBody:\n40 gloves M"


def test_seeded_customer_emails_are_unique_ignoring_case(business_db):
    emails = [r[0] for r in business_db.execute("SELECT lower(email) FROM customers")]
    assert len(emails) == len(set(emails))


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "no sender address in the From header"),
        (b"Subject: Order\r\n\r\n40 gloves", "no sender address in the From header"),
        (b"From: not an address\r\nSubject: Order\r\n\r\n40 gloves", "no sender address in the From header"),
        (b"\x00\xff\xfe binary junk \x00", "the email cannot be parsed"),
    ],
)
def test_unparseable_email_is_rejected(tmp_path, business_db, content, reason):
    path = tmp_path / "bad.eml"
    path.write_bytes(content)
    with pytest.raises(EmailRejected, match=reason):
        read_email(path, business_db)


def test_unreadable_file_is_rejected(tmp_path, business_db):
    with pytest.raises(EmailRejected, match="the email file 'missing.eml' cannot be read"):
        read_email(tmp_path / "missing.eml", business_db)


def test_broken_attachment_is_rejected(tmp_path, business_db):
    path = make_email(tmp_path / "a.eml", SENDER, attachments=[("order.pdf", b"not a pdf", "application/pdf")])
    with pytest.raises(EmailRejected, match="attachment 'order.pdf' cannot be read"):
        read_email(path, business_db)


def test_unknown_sender_is_rejected(tmp_path, business_db):
    path = make_email(tmp_path / "a.eml", "stranger@example.org", "Order", "40 gloves M")
    with pytest.raises(EmailRejected, match="the sender 'stranger@example.org' is not a known customer"):
        read_email(path, business_db)


def test_over_long_email_is_rejected(tmp_path, business_db):
    path = make_email(tmp_path / "a.eml", SENDER, "Order", "gloves " * (MAX_EMAIL_TEXT // 7 + 1))
    with pytest.raises(EmailRejected, match=f"above the limit of {MAX_EMAIL_TEXT}"):
        read_email(path, business_db)

import json
import socket

import pytest

from purchase_cycle import db
from purchase_cycle.llm import EXTRACTION, build_system_prompt, recording_key

SENTENCE = "Please send 40 boxes of powder-free nitrile gloves, size M"


@pytest.fixture
def seeded_db(tmp_path):
    path = tmp_path / "business.db"
    conn = db.connect(path)
    db.seed(conn)
    catalog = db.catalog_rows(conn)
    conn.close()
    return path, catalog


@pytest.fixture
def write_recording(tmp_path):
    """Store one recorded answer for a sentence and return the recordings path."""

    def _write(catalog, sentence, answer, case_id="TEST-1", task=EXTRACTION):
        path = tmp_path / "recordings.jsonl"
        key = recording_key(build_system_prompt(catalog, task), sentence, task)
        row = {"key": key, "case_id": case_id, "sentence": sentence, "answer": answer}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return path

    return _write


@pytest.fixture
def no_network(monkeypatch):
    """Fail on any outgoing connection and record the attempts."""
    attempts = []

    def guard(*args, **kwargs):
        attempts.append(args)
        raise OSError("network blocked by test")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    return attempts


def text_pdf(pages: list[list[str]]) -> bytes:
    """Smallest valid PDF with a text layer, one list of lines per page."""
    count = len(pages)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(count))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R "
            "/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        shown = " T* ".join(f"({line})Tj" for line in lines)
        stream = f"BT /F1 12 Tf 14 TL 72 720 Td {shown} ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def xlsx_bytes(sheets: dict[str, list[list]]) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_email(path, sender, subject="Order", body="Hello", html=None, attachments=()):
    """Write an .eml file; attachments are (file name, bytes, mime type) triples."""
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = sender
    message["To"] = "orders@example.com"
    message["Subject"] = subject
    if body is not None:
        message.set_content(body)
    if html is not None and body is None:
        message.set_content(html, subtype="html")
    elif html is not None:
        message.add_alternative(html, subtype="html")
    for name, data, mime in attachments:
        maintype, subtype = mime.split("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    path.write_bytes(bytes(message))
    return path


@pytest.fixture
def invoke_durability(monkeypatch):
    """Record the `durability` of every checkpointed graph run (C10: phase 05 entry points run with "sync")."""
    from langgraph.pregel import Pregel

    seen = []
    original = Pregel.invoke

    def spy(self, *args, **kwargs):
        if self.checkpointer is not None:
            seen.append(kwargs.get("durability"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Pregel, "invoke", spy)
    return seen

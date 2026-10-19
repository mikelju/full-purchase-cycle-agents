import json
import os
import socket
import subprocess
import sys
import textwrap

import pytest

from purchase_cycle import claims, db, llm
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


# Loaded by every Python process the test starts, the demo and its crash and resume children: it notes the process
# and blocks the network, noting each attempt.
SITECUSTOMIZE = textwrap.dedent(
    """
    import os, socket

    LOG = os.environ["ORDERS_DEMO_TEST_LOG"]

    def note(text):
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(text + "\\n")

    def blocked(*args, **kwargs):
        note("network")
        raise OSError("network blocked by test")

    note("process")
    socket.socket.connect = blocked
    socket.create_connection = blocked
    """
)

SKU = "GLV-NIT-XS"


def _invoice(conn, number, result, rows=(), supplier="SUP-001"):
    """A reconciled invoice with its purchase order and discrepancy rows; returns the invoice id."""
    conn.execute(
        "INSERT INTO supplier_documents (kind, number, source_file, content_hash, status, created_at) "
        "VALUES ('invoice', ?, 'f.txt', ?, 'linked', 't')",
        (number, number),
    )
    invoice = conn.execute("SELECT MAX(id) FROM supplier_documents").fetchone()[0]
    conn.execute(
        "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (?, 5, ?, 'ordered', 't', 't', 't')",
        (SKU, f"th-{number}"),
    )
    conn.execute(
        "INSERT INTO purchase_orders (request_id, supplier_code, sku, quantity, unit_price, currency, total, "
        "expected_delivery, issued_at, status) "
        "VALUES ((SELECT MAX(id) FROM quote_requests), ?, ?, 5, 1.0, 'EUR', 5.0, 't', 't', 'issued')",
        (supplier, SKU),
    )
    conn.execute(
        "INSERT INTO reconciliations (invoice_id, purchase_order_id, result, reconciled_at) "
        "VALUES (?, (SELECT MAX(id) FROM purchase_orders), ?, 't')",
        (invoice, result),
    )
    reconciliation_id = conn.execute("SELECT MAX(id) FROM reconciliations").fetchone()[0]
    for sku, kind, expected, found, unit_amount, amount, text in rows:
        conn.execute(
            "INSERT INTO discrepancies (reconciliation_id, sku, type, expected, found, unit_amount, amount, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (reconciliation_id, sku, kind, expected, found, unit_amount, amount, text),
        )
    conn.commit()
    return invoice


ROWS = [(SKU, "price", 4.0, 4.5, 0.5, 5.0, None), (None, "extra_line", None, 3.0, 2.0, 6.0, "Surgical masks")]


def _two_claims_from(conn, tmp_path, facts, bodies):
    path = tmp_path / "two.jsonl"
    with path.open("w") as fh:
        for fact, body in zip(facts, bodies, strict=True):
            key = llm.recording_key(
                llm.build_system_prompt([], llm.CLAIM_DRAFTING), claims.facts_prompt(fact), llm.CLAIM_DRAFTING
            )
            answer = body if isinstance(body, dict) else {"body": body}
            fh.write(json.dumps({"key": key, "case_id": "t", "sentence": "", "answer": answer}) + "\n")
    return facts, llm.ModelClient("replay", [], path, task=llm.CLAIM_DRAFTING)


def run_reconcile_demo(tmp_path, *extra):
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    log = tmp_path / "processes.log"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LANGSMITH_API_KEY")}
    env.update(PYTHONPATH=str(site), ORDERS_DEMO_TEST_LOG=str(log))
    workdir = tmp_path / "work"
    command = [sys.executable, "-m", "purchase_cycle.cli", "reconcile-demo", "--workdir", str(workdir), *extra]
    run = subprocess.run(command, capture_output=True, text=True, env=env)
    return run, log, workdir


@pytest.fixture(scope="session")
def demo_without_claim_recordings(tmp_path_factory):
    """One demo run with a missing claim recordings file, shared by the tests that only read its output and database."""
    base = tmp_path_factory.mktemp("demo_without_claim_recordings")
    run, _, workdir = run_reconcile_demo(base, "--claim-recordings", str(base / "none.jsonl"))
    return run, workdir


@pytest.fixture(scope="session")
def claim_recordings(tmp_path_factory, demo_without_claim_recordings):
    """A claim recordings file for the four demo prompts, built from the phase 08 part of a demo run that has none."""
    base = tmp_path_factory.mktemp("claim_recordings")
    run, workdir = demo_without_claim_recordings
    assert run.returncode == 1 and "claim recordings not found" in run.stderr
    conn = db.connect(workdir / "business.db")
    try:
        facts = [claims.claim_facts(conn, invoice_id) for invoice_id in claims.candidates(conn)]
    finally:
        conn.close()
    path = base / "claim_demo.jsonl"
    system = llm.build_system_prompt([], llm.CLAIM_DRAFTING)
    with path.open("w", encoding="utf-8") as fh:
        for fact in facts:
            key = llm.recording_key(system, claims.facts_prompt(fact), llm.CLAIM_DRAFTING)
            row = {
                "key": key,
                "case_id": f"claim-{fact['invoice']}",
                "sentence": "",
                "answer": {"body": claims.fill_template(fact)},
            }
            fh.write(json.dumps(row) + "\n")
    return path


@pytest.fixture(scope="session")
def reconcile_demo(tmp_path_factory, claim_recordings):
    """One full demo run with the claim recordings, shared by the tests that only read its output, log and database."""
    return run_reconcile_demo(tmp_path_factory.mktemp("reconcile_demo"), "--claim-recordings", str(claim_recordings))


@pytest.fixture(autouse=True)
def _eval_summaries_in_tmp(tmp_path, monkeypatch):
    """Evaluation tests never write into the real data/eval_summaries folder."""
    from purchase_cycle.evaluation import harness

    monkeypatch.setattr(harness, "SUMMARIES_DIR", tmp_path / "eval_summaries_default")

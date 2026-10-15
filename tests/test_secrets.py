"""C14 (phases 01 to 03), C16 (phase 04) and C18 (phase 05): no secret is versioned and every variable is documented."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{10,}|lsv2_[A-Za-z0-9_]{10,}|x-api-key|authorization:\s*bearer", re.I
)
EXAMPLE = ROOT / (".env" + ".example")


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_env_file_is_not_tracked():
    tracked = _tracked()
    assert ".env" not in tracked
    assert EXAMPLE.name in tracked


def test_example_lists_every_variable_without_values():
    lines = [line for line in EXAMPLE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    names = {}
    for line in lines:
        name, _, value = line.partition("=")
        names[name] = value
    assert all(value == "" for value in names.values())
    # Variables read by our code, plus the ones the Anthropic and LangSmith clients read.
    used = set()
    for path in (ROOT / "src").rglob("*.py"):
        used |= set(re.findall(r"environ(?:\.get)?[\[(]\s*\"([A-Z_]+)\"", path.read_text(encoding="utf-8")))
    used -= {"LANGCHAIN_TRACING_V2"}  # legacy name, only cleared
    expected = {"ANTHROPIC_API_KEY", "LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGSMITH_PROJECT"} | used
    assert expected <= set(names)


def test_tracked_files_and_recordings_hold_no_keys():
    for name in _tracked():
        path = ROOT / name
        if path.suffix in {".png", ".jpg", ".ico"} or not path.is_file() or path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not SECRET_PATTERNS.search(text), f"possible secret in {name}"


def test_phase_02_data_files_are_tracked_and_scanned():
    tracked = set(_tracked())
    for name in (
        "evals/recordings/web_form_matching.jsonl",
        "evals/datasets/web_form_matching/dataset.jsonl",
        "evals/baselines/web_form_matching.json",
        "examples/web_form_submission.json",
    ):
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"


def test_phase_03_files_are_tracked_and_their_decoded_text_holds_no_keys():
    from purchase_cycle.email_order import model_text, parse_email

    tracked = set(_tracked())
    dataset = "evals/datasets/email_order_extraction"
    fixed = [
        "evals/recordings/email_intake.jsonl",
        "evals/recordings/email_order_extraction.jsonl",
        f"{dataset}/plan.jsonl",
        f"{dataset}/dataset.jsonl",
        f"{dataset}/second_pass_review.jsonl",
        "evals/baselines/email_order_extraction.json",
        "evals/audit/email_order_extraction-audit-v1.0.csv",
    ]
    texts = sorted(str(p.relative_to(ROOT).as_posix()) for p in (ROOT / dataset / "texts").iterdir())
    emails = sorted(
        str(p.relative_to(ROOT).as_posix())
        for folder in (ROOT / dataset / "emails", ROOT / "examples" / "email_orders")
        for p in folder.glob("*.eml")
    )
    assert len(emails) >= 312 + 4 and texts  # the dataset emails plus the demo samples
    for name in fixed + texts + emails:
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"
        assert not SECRET_PATTERNS.search((ROOT / name).read_text(encoding="utf-8", errors="ignore")), name
    attachments = 0
    for name in emails:
        email = parse_email((ROOT / name).read_bytes())
        attachments += sum(
            a["name"].endswith((".pdf", ".xlsx")) and bool(a["text"].strip()) for a in email["attachments"]
        )
        decoded = "\n".join([email["sender"], model_text(email)])
        assert not SECRET_PATTERNS.search(decoded), f"possible secret in the decoded text of {name}"
    assert attachments >= 104, "the PDF and Excel attachments were not decoded"


def test_phase_04_files_are_tracked_and_their_decoded_text_holds_no_keys():
    from purchase_cycle.email_order import model_text, parse_email

    tracked = set(_tracked())
    datasets = ("evals/datasets/clarification_detection", "evals/datasets/clarification_answers")
    fixed = [
        "evals/recordings/clarification_detection_matching.jsonl",
        "evals/recordings/clarification_detection_email_intake.jsonl",
        "evals/recordings/clarification_detection_email_extraction.jsonl",
        "evals/recordings/clarification_question.jsonl",
        "evals/recordings/clarification_answers.jsonl",
        "evals/baselines/clarification_detection.json",
        "evals/baselines/clarification_answers.json",
        "evals/audit/clarification-audit-v1.0.csv",
        "examples/exceptions/web_form_submission.json",
        "examples/exceptions/web_form_answer.txt",
        "examples/exceptions/email_answer.txt",
    ]
    fixed += [f"{d}/{name}" for d in datasets for name in ("plan.jsonl", "dataset.jsonl", "second_pass_review.jsonl")]
    written = sorted(
        str(p.relative_to(ROOT).as_posix())
        for d in datasets
        for folder in ("texts", "second_pass_judgments")
        for p in (ROOT / d / folder).iterdir()
    )
    emails = sorted(
        str(p.relative_to(ROOT).as_posix())
        for folder in (ROOT / datasets[0] / "emails", ROOT / "examples" / "exceptions")
        for p in folder.glob("*.eml")
    )
    assert len(written) >= 8 and len(emails) >= 100 + 1  # the detection emails plus the demo sample
    for name in fixed + written + emails:
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"
        assert not SECRET_PATTERNS.search((ROOT / name).read_text(encoding="utf-8", errors="ignore")), name
    for name in emails:
        email = parse_email((ROOT / name).read_bytes())
        decoded = "\n".join([email["sender"], model_text(email)])
        assert not SECRET_PATTERNS.search(decoded), f"possible secret in the decoded text of {name}"


def test_phase_05_files_are_tracked_and_hold_no_keys():
    tracked = set(_tracked())
    whatsapp = "evals/datasets/whatsapp_order_extraction"
    fixed = [
        "evals/recordings/whatsapp_intake.jsonl",
        "evals/recordings/whatsapp_order_extraction.jsonl",
        "evals/recordings/order_scenarios.jsonl",
        "evals/recordings/orders_demo.jsonl",
        f"{whatsapp}/plan.jsonl",
        f"{whatsapp}/dataset.jsonl",
        f"{whatsapp}/second_pass_review.jsonl",
        "evals/datasets/channel_routing/dataset.jsonl",
        "evals/datasets/failure_recovery/dataset.jsonl",
        "evals/baselines/whatsapp_order_extraction.json",
        "evals/baselines/order_scenarios.json",
        "evals/audit/whatsapp_order_extraction-audit-v1.0.csv",
    ]
    folders = (
        ROOT / whatsapp / "texts",
        ROOT / whatsapp / "messages",
        ROOT / "evals" / "datasets" / "order_scenarios",
        ROOT / "examples" / "orders",
    )
    files = sorted(str(p.relative_to(ROOT).as_posix()) for folder in folders for p in folder.rglob("*") if p.is_file())
    assert len(files) >= 5 + 160 + 30 + 8  # texts, messages, scenarios and the sample inbox with its crash item
    for name in fixed + files:
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"
        assert not SECRET_PATTERNS.search((ROOT / name).read_text(encoding="utf-8", errors="ignore")), name
    # Outbox files are written under the ignored data/ folder at run time; none is versioned.
    assert not [name for name in tracked if "outbox" in name.split("/")[0:2]]


def test_phase_06_files_are_tracked_and_hold_no_keys(tmp_path):
    from purchase_cycle import db, quotes, sim_clock
    from purchase_cycle.graph import sqlite_checkpointer

    tracked = set(_tracked())
    fixed = ["src/purchase_cycle/suppliers.py", "src/purchase_cycle/quotes.py"]
    folders = (ROOT / "evals" / "datasets" / "quote_wait", ROOT / "examples" / "quotes")
    files = sorted(str(p.relative_to(ROOT).as_posix()) for folder in folders for p in folder.rglob("*") if p.is_file())
    assert len(files) >= 20 + 3  # the timelines, the sample orders and the two sample replies
    for name in fixed + files:
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"
        assert not SECRET_PATTERNS.search((ROOT / name).read_text(encoding="utf-8", errors="ignore")), name
    # Outbox emails are written at run time and never versioned: render a request and a reminder and scan them.
    path, outbox = tmp_path / "business.db", tmp_path / "supplier_outbox"
    conn = db.connect(path)
    try:
        db.seed(conn)
        db.insert_order(conn, "CLI-001", "web_form", "received", [("GLV-NIT-XS", 12)])
    finally:
        conn.close()
    saver = sqlite_checkpointer(tmp_path / "checkpoints.sqlite")
    assert quotes.scan(path, saver, outbox)
    conn = db.connect(path)
    try:
        sim_clock.advance(conn, days=3)
    finally:
        conn.close()
    quotes.tick(path, saver, outbox)
    written = sorted(outbox.glob("*.eml"))
    assert {p.name.split("-")[2] for p in written} == {"request", "reminder"}
    for email in written:
        assert not SECRET_PATTERNS.search(email.read_text(encoding="utf-8", errors="ignore")), email.name


def test_phase_07_files_are_tracked_and_hold_no_keys(tmp_path):
    from test_quote_decision_graph import World

    tracked = set(_tracked())
    fixed = [
        "src/purchase_cycle/quote_decision.py",
        "evals/recordings/offer_extraction.jsonl",
        "evals/recordings/quotes_demo.jsonl",
        "evals/baselines/offer_extraction.json",
    ]
    folders = (
        ROOT / "evals" / "datasets" / "offer_extraction",
        ROOT / "evals" / "datasets" / "quote_decision",
        ROOT / "examples" / "quotes",
    )
    files = sorted(str(p.relative_to(ROOT).as_posix()) for folder in folders for p in folder.rglob("*") if p.is_file())
    assert len(files) >= 37 + 3 + 4  # the scenarios, the sample orders and replies, and the dataset files
    assert "examples/quotes/replies-day-3/QR-0001-SUP-003.eml" in files
    for name in fixed + files:
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"
        assert not SECRET_PATTERNS.search((ROOT / name).read_text(encoding="utf-8", errors="ignore")), name
    # Purchase order emails are written at run time and never versioned: render one and scan it.
    world = World(tmp_path)
    assert world.run("decide") == 0
    assert world.run("approve", "QR-0001") == 0
    (email,) = world.emails()
    assert email.name.startswith("PO-0001-")
    assert not SECRET_PATTERNS.search(email.read_text(encoding="utf-8", errors="ignore"))


def test_phase_08_files_are_tracked_and_hold_no_keys():
    from purchase_cycle import reconciliation

    tracked = set(_tracked())
    fixed = [
        "src/purchase_cycle/reconciliation.py",
        "src/purchase_cycle/reconcile_match.py",
        "src/purchase_cycle/evaluation/document_dataset.py",
        "src/purchase_cycle/evaluation/document_eval.py",
        "src/purchase_cycle/evaluation/reconcile_eval.py",
        "evals/recordings/document_extraction.jsonl",
        "evals/recordings/reconcile_demo.jsonl",
        "evals/baselines/document_extraction.json",
    ]
    folders = (
        ROOT / "evals" / "datasets" / "document_extraction",
        ROOT / "evals" / "datasets" / "reconciliation_matching",
        ROOT / "examples" / "invoices",
    )
    files = sorted(str(p.relative_to(ROOT).as_posix()) for folder in folders for p in folder.rglob("*") if p.is_file())
    assert len([f for f in files if f.startswith("evals/datasets/reconciliation_matching/RM-")]) >= 40
    assert len([f for f in files if f.startswith("examples/invoices/")]) == 17  # orders, 15 documents, claim_edit.txt
    assert {Path(f).suffix for f in files if f.startswith("examples/invoices/documents/")} == {".txt", ".pdf", ".xlsx"}
    for name in fixed + files:
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"
        assert not SECRET_PATTERNS.search((ROOT / name).read_text(encoding="utf-8", errors="ignore")), name
    # A PDF or Excel file is compressed, so its decoded text is what the scan reads.
    decoded = 0
    for name in files:
        if name.startswith(("examples/invoices/", "evals/datasets/document_extraction/files/")) and Path(
            name
        ).suffix in (".pdf", ".xlsx"):
            decoded += 1
            assert not SECRET_PATTERNS.search(reconciliation.document_text(ROOT / name)), name
    assert decoded >= 40 + 6, "the rendered dataset files and the sample PDF and Excel documents were not decoded"


def test_phase_09_files_are_tracked_and_hold_no_keys():
    tracked = set(_tracked())
    fixed = [
        "src/purchase_cycle/claims.py",
        "src/purchase_cycle/review.py",
        "src/purchase_cycle/evaluation/claim_dataset.py",
        "src/purchase_cycle/evaluation/claim_eval.py",
        "src/purchase_cycle/evaluation/claim_approval_eval.py",
        "evals/recordings/claim_drafting.jsonl",
        "evals/recordings/claim_demo.jsonl",
        "evals/baselines/claim_drafting.json",
        "examples/invoices/claim_edit.txt",
    ]
    folders = (
        ROOT / "evals" / "datasets" / "claim_drafting",
        ROOT / "evals" / "datasets" / "claim_approval",
    )
    files = sorted(str(p.relative_to(ROOT).as_posix()) for folder in folders for p in folder.rglob("*") if p.is_file())
    assert len([f for f in files if f.startswith("evals/datasets/claim_approval/CA-")]) >= 24
    assert len([f for f in files if f.startswith("evals/datasets/claim_drafting/")]) >= 1
    for name in fixed + files:
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"
        assert not SECRET_PATTERNS.search((ROOT / name).read_text(encoding="utf-8", errors="ignore")), name


def test_phase_10_files_are_tracked_and_hold_no_keys():
    tracked = set(_tracked())
    fixed = [
        "src/purchase_cycle/receipts.py",
        "src/purchase_cycle/receipts_cli.py",
        "src/purchase_cycle/evaluation/goods_receipt_eval.py",
        "examples/receipts/customer_orders.json",
        "examples/receipts/purchase_orders.json",
    ]
    folder = ROOT / "evals" / "datasets" / "goods_receipt"
    files = sorted(str(p.relative_to(ROOT).as_posix()) for p in folder.rglob("*") if p.is_file())
    assert len(files) >= 30
    for name in fixed + files:
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"
        assert not SECRET_PATTERNS.search((ROOT / name).read_text(encoding="utf-8", errors="ignore")), name
    # The sample documents of phase 08 stay as they were: no file is added under examples/invoices/.
    assert len([f for f in tracked if f.startswith("examples/invoices/")]) == 17

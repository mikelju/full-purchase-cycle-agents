"""Command line of the orchestrator: `cycle run`, one pass over the whole purchase cycle, and `cycle-demo`."""

import shutil
import subprocess
import sys
from pathlib import Path

from purchase_cycle import config, cycle, db
from purchase_cycle.llm import MissingRecording, ModelClient

DEMO_CYCLE = config.ROOT / "examples" / "cycle"
CYCLE_DEMO_WORKDIR = config.DATA_DIR / "cycle-demo"
CYCLE_DEMO_RECORDINGS = config.EVALS_DIR / "recordings" / "cycle_demo.jsonl"  # one file for every task
CYCLE_DEMO_ORDERS = {1: "A", 4: "B", 2: "C", 3: "D"}  # order id -> scenario letter
DECISION_COMMANDS = ("clarify", "quotes", "reconcile", "sales")

STAGE_LABELS = {
    "orders": {"items": "items", "stored": "stored", "paused": "paused", "failed": "failed"},
    "quotes": {
        "started": "started",
        "accepted": "replies accepted",
        "waiting": "waiting",
        "decided": "decided",
        "no_offers": "without offers",
    },
    "reconcile": {"documents": "documents", "new": "new", "claims": "claims drafted"},
    "receipts": {"received": "received", "served": "served", "issued": "issued"},
    "sales": {
        "invoiced": "invoiced",
        "signed_notes": "signed notes",
        "reconciled": "reconciled",
        "corrections": "corrections",
        "responses": "responses drafted",
    },
}


def cmd_cycle_run(args) -> int:
    clients = []

    def client(task, catalog):
        made = ModelClient(args.mode, catalog, args.recordings, task=task)
        clients.append(made)
        return made

    folders = cycle.Folders(
        args.orders,
        args.replies,
        args.documents,
        args.signed_notes,
        args.outbox,
        args.supplier_outbox,
        args.delivery_notes,
        args.invoices,
    )
    try:
        result = cycle.run_cycle(args.db, args.checkpoints, folders, client)
    except MissingRecording as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    saved = sum(made.save_recordings() for made in clients)
    kind = "resumed" if result["resumed"] else "new"
    print(f"mode={args.mode}  pass={result['thread_id']} ({kind})")
    for stage in cycle.STAGES:
        counts = result["counts"].get(stage, {})
        done = [f"{counts[key]} {label}" for key, label in STAGE_LABELS[stage].items() if counts.get(key)]
        print(f"  {stage}: " + (", ".join(done) if done else "nothing new"))
    pending = result["pending"]
    print(f"pending decisions: {len(pending)}")
    for item in pending:
        print(f"  {item['kind']} {item['reference']}")
        for command in item["commands"]:
            print(f"    {command}")
    if saved:
        print(f"recordings saved: {saved}")
    return 0


def cmd_cycle_demo(args) -> int:
    """The four-order scenario through the whole cycle, each step in its own process."""
    examples, workdir = Path(args.examples), Path(args.workdir)
    if workdir.resolve() == config.DATA_DIR.resolve():
        print(f"Error: --workdir {args.workdir} is the data folder; pick another folder", file=sys.stderr)
        return 1
    recordings = Path(args.recordings)
    if args.mode == "replay" and not recordings.is_file():
        print(f"Error: cycle recordings not found: {recordings}; record them once with --mode record", file=sys.stderr)
        return 1
    folders = {name: workdir / name for name in ("orders", "replies", "documents", "signed")}
    db_path, checkpoints = workdir / "business.db", workdir / "cycle.sqlite"
    workdir.mkdir(parents=True, exist_ok=True)
    for path in (db_path, checkpoints):
        path.unlink(missing_ok=True)
    for name in ("outbox", "supplier_outbox", "delivery_notes", "invoices", "customer_outbox", *folders):
        shutil.rmtree(workdir / name, ignore_errors=True)
    for folder in folders.values():
        folder.mkdir()
    shutil.copytree(examples / "orders", folders["orders"], dirs_exist_ok=True)
    conn = db.connect(db_path)
    try:
        db.seed(conn)
        conn.execute("UPDATE stock SET on_hand = 0 WHERE sku IN ('GLV-NIT-S', 'GLV-NIT-XS')")
        conn.commit()
    finally:
        conn.close()
    print(
        f"cycle-demo  mode={args.mode}  examples={args.examples}  workdir={args.workdir}  recordings={args.recordings}"
    )
    number = 0

    def step(*command) -> bool:
        nonlocal number
        number += 1
        run = subprocess.run(
            [sys.executable, "-m", "purchase_cycle.cli", "--db", str(db_path), *command],
            capture_output=True,
            text=True,
        )
        print()
        who = "operator decision: " if command[0] in DECISION_COMMANDS else ""
        print(
            f"== step {number}: {who}purchase-cycle {' '.join(command[:2])}  (own process, exit code {run.returncode})"
        )
        if run.returncode != 0:
            print(f"Error: step {number} failed: {run.stderr.strip()}", file=sys.stderr)
            return False
        for line in run.stdout.splitlines():
            print(f"  {line}")
        return True

    cp = ["--checkpoints", str(checkpoints)]
    model = ["--mode", args.mode, "--recordings", str(recordings)]
    sup_out = ["--outbox", str(workdir / "supplier_outbox")]
    cycle_run = [
        "cycle", "run", "--orders", str(folders["orders"]), "--replies", str(folders["replies"]),
        "--documents", str(folders["documents"]), "--signed-notes", str(folders["signed"]),
        "--outbox", str(workdir / "outbox"), "--supplier-outbox", str(workdir / "supplier_outbox"),
        "--delivery-notes", str(workdir / "delivery_notes"), "--invoices", str(workdir / "invoices"),
        *model, *cp,
    ]  # fmt: skip

    def copy(source: str, target: Path) -> None:
        shutil.copytree(examples / source, target, dirs_exist_ok=True)

    if not step(*cycle_run):
        return 1
    conn = db.connect(db_path)
    try:
        thread = conn.execute("SELECT thread_id FROM clarifications ORDER BY rowid LIMIT 1").fetchone()
    finally:
        conn.close()
    if thread is None:
        print("Error: the first pass left no clarification to answer", file=sys.stderr)
        return 1
    reply = (examples / "clarification_reply.txt").read_text(encoding="utf-8-sig").strip()
    if not step("clarify", "answer", thread[0], "--text", reply, *model, *cp, "--outbox", str(workdir / "outbox")):
        return 1
    if not step(*cycle_run):
        return 1
    copy("replies", folders["replies"])
    if not step("clock", "advance", "--days", "6") or not step(*cycle_run):
        return 1
    for request in ("QR-0001", "QR-0002"):
        if not step("quotes", "approve", request, *cp, *sup_out):
            return 1
    copy("documents", folders["documents"])
    if not step(*cycle_run):
        return 1
    if not step("reconcile", "approve", "INV-0002", *cp, *sup_out) or not step("reconcile", "reject", "INV-0004", *cp):
        return 1
    if not step(*cycle_run):
        return 1
    copy("signed_notes", folders["signed"])
    if not step(*cycle_run):
        return 1
    invoices = ["--invoices-dir", str(workdir / "invoices"), "--outbox", str(workdir / "customer_outbox")]
    if not step("sales", "approve", "SINV-0002", *cp, *invoices) or not step(*cycle_run):
        return 1
    conn = db.connect(db_path)
    try:
        lines = cycle_demo_summary(conn)
    finally:
        conn.close()
    print()
    print("summary:")
    for line in lines:
        print(line)
    return 0


def cycle_demo_summary(conn) -> list[str]:
    """One line per order, then the supplier side of the scenario."""
    lines = []
    for order_id, status in conn.execute("SELECT id, status FROM orders ORDER BY id").fetchall():
        invoice = conn.execute(
            "SELECT s.result, r.status FROM customer_invoices i"
            " LEFT JOIN sales_reconciliations s ON s.invoice_id = i.id"
            " LEFT JOIN customer_responses r ON r.reconciliation_id = s.id WHERE i.order_id = ?",
            (order_id,),
        ).fetchone()
        sales = "not invoiced" if invoice is None else f"invoice {invoice[0] or 'open'}"
        response = f", credit note {invoice[1]}" if invoice and invoice[1] else ""
        lines.append(f"order {order_id} ({CYCLE_DEMO_ORDERS.get(order_id, '?')}): {status}, {sales}{response}")
    claims = conn.execute("SELECT status, COUNT(*) FROM claims GROUP BY status ORDER BY status").fetchall()
    lines.append("supplier claims: " + (", ".join(f"{n} {name}" for name, n in claims) or "none"))
    waiting = conn.execute("SELECT id, sku, quantity FROM quote_requests WHERE status = 'waiting'").fetchall()
    lines.append("quote requests waiting: " + (", ".join(f"QR-{i:04d} {s} x{q}" for i, s, q in waiting) or "none"))
    return lines


def add_cycle_commands(sub) -> None:
    cycle_parser = sub.add_parser("cycle", help="run the whole purchase cycle, one pass at a time")
    cycle_sub = cycle_parser.add_subparsers(dest="cycle_command", required=True)
    run = cycle_sub.add_parser(
        "run", help="one pass: orders, quotes, reconcile, receipts and sales; lists the decisions left to a person"
    )
    run.add_argument("--orders", required=True, help="inbox of web form, email and WhatsApp items")
    run.add_argument("--replies", required=True, help="folder of supplier quote replies (.eml)")
    run.add_argument("--documents", required=True, help="folder of supplier invoices and delivery notes")
    run.add_argument("--signed-notes", required=True, help="folder of signed customer delivery notes")
    run.add_argument("--outbox", default=str(config.OUTBOX_DIR), help="folder for the WhatsApp reply files")
    run.add_argument(
        "--supplier-outbox", default=str(config.SUPPLIER_OUTBOX_DIR), help="folder for the supplier emails"
    )
    run.add_argument(
        "--delivery-notes",
        default=str(config.CUSTOMER_DELIVERY_NOTES_DIR),
        help="folder for the customer delivery notes",
    )
    run.add_argument("--invoices", default=str(config.CUSTOMER_INVOICES_DIR), help="folder for the customer invoices")
    run.add_argument("--mode", choices=config.MODES, default="replay")
    run.add_argument("--recordings", default=None, help="one recordings file for every model task")
    run.add_argument("--checkpoints", default=str(config.default_checkpoint_path()))
    run.set_defaults(handler=cmd_cycle_run)

    demo = sub.add_parser(
        "cycle-demo", help="run the four-order scenario through the whole cycle, each step in its own process"
    )
    demo.add_argument("--examples", default=str(DEMO_CYCLE), help="folder of the scenario inputs")
    demo.add_argument("--workdir", default=str(CYCLE_DEMO_WORKDIR), help="folder for the demo database and documents")
    demo.add_argument("--recordings", default=str(CYCLE_DEMO_RECORDINGS), help="one recordings file for every task")
    demo.add_argument("--mode", choices=config.MODES, default="replay", help="record needs ANTHROPIC_API_KEY")
    demo.set_defaults(handler=cmd_cycle_demo)

"""Command line of the goods receipt step: `receipts run`, `receipts list` and `receipts show`."""

import re
import sys
from pathlib import Path

from purchase_cycle import config, db, receipts, reconciliation
from purchase_cycle.graph import sqlite_checkpointer


def _seeded(path):
    conn = db.connect(path)
    db.seed(conn)
    return conn


def _units(lines) -> str:
    return " ".join(f"{sku}={quantity}" for sku, quantity in lines)


def cmd_receipts_run(args) -> int:
    conn = _seeded(args.db)
    conn.close()
    checkpointer = sqlite_checkpointer(args.checkpoints)
    try:
        state = receipts.run_receipts(args.db, args.notes_dir, checkpointer)
    finally:
        checkpointer.conn.close()
    done = state.get("receipts", [])
    served = state.get("served", [])
    skipped = state.get("skipped", [])
    issued = state.get("issued", [])
    for item in done:
        what = _units(item["lines"]) if item["status"] == "received" else f"reason: {item['reason']}"
        print(
            f"  receipt RCP-{item['receipt_id']:04d}  {item['number'] or '-'}  "
            f"{reconciliation.order_reference(item['purchase_order_id'])}  {item['status']}  {what}"
        )
    for item in served:
        print(f"  served ORD-{item['order_id']:04d}  CDN-{item['note_id']:04d}  {_units(item['lines'])}")
    for item in skipped:
        print(f"  skipped ORD-{item['order_id']:04d}  missing {_units(item['missing'].items())}")
    for path in issued:
        print(f"  delivery note file: {path}")
    if not (done or served or issued):
        print("nothing changed")
    return 0


def cmd_receipts_list(args) -> int:
    conn = _seeded(args.db)
    try:
        rows = conn.execute(
            "SELECT g.id, g.purchase_order_id, g.status, g.received_at, d.number FROM goods_receipts g "
            "JOIN supplier_documents d ON d.id = g.delivery_note_id ORDER BY g.id"
        ).fetchall()
        print(f"receipts: {len(rows)}")
        for row in rows:
            lines = conn.execute(
                "SELECT sku, quantity FROM goods_receipt_lines WHERE receipt_id = ? ORDER BY line_no", (row["id"],)
            ).fetchall()
            print(
                f"  RCP-{row['id']:04d}  note {row['number'] or '-'}  "
                f"{reconciliation.order_reference(row['purchase_order_id'])}  {row['status']}  "
                f"{row['received_at']}  {_units((r['sku'], r['quantity']) for r in lines)}".rstrip()
            )
        notes = conn.execute(
            "SELECT n.id, n.order_id, n.customer_code, n.issued_at, c.name FROM customer_delivery_notes n "
            "JOIN customers c ON c.code = n.customer_code ORDER BY n.id"
        ).fetchall()
        print(f"customer delivery notes: {len(notes)}")
        for note in notes:
            lines = conn.execute(
                "SELECT sku, quantity FROM customer_delivery_note_lines WHERE note_id = ? ORDER BY line_no",
                (note["id"],),
            ).fetchall()
            print(
                f"  CDN-{note['id']:04d}  ORD-{note['order_id']:04d}  customer={note['customer_code']} ({note['name']})  "
                f"{note['issued_at']}  {_units((r['sku'], r['quantity']) for r in lines)}"
            )
    finally:
        conn.close()
    return 0


def cmd_receipts_show(args) -> int:
    found = re.fullmatch(r"CDN-(\d+)", args.reference.strip(), re.IGNORECASE)
    if found is None:
        print(f"Error: '{args.reference}' is not a delivery note reference like CDN-0001", file=sys.stderr)
        return 1
    conn = _seeded(args.db)
    try:
        row = conn.execute(
            "SELECT document_file FROM customer_delivery_notes WHERE id = ?", (int(found.group(1)),)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        print(f"Error: no customer delivery note {args.reference}", file=sys.stderr)
        return 1
    path = Path(row["document_file"])
    if not path.exists():
        print(
            f"Error: the file of {args.reference} is missing: {path}; run purchase-cycle receipts run", file=sys.stderr
        )
        return 1
    print(path.read_text(encoding="ascii"), end="")
    return 0


def add_receipts_commands(sub) -> None:
    parser = sub.add_parser("receipts", help="put reconciled supplier goods into stock and serve the waiting orders")
    receipts_sub = parser.add_subparsers(dest="receipts_command", required=True)
    run = receipts_sub.add_parser("run", help="receive the reconciled delivery notes, serve the orders stock covers")
    run.add_argument("--checkpoints", default=str(config.default_checkpoint_path()))
    run.add_argument(
        "--notes-dir", default=str(config.CUSTOMER_DELIVERY_NOTES_DIR), help="folder for the customer delivery notes"
    )
    run.set_defaults(handler=cmd_receipts_run)
    receipts_sub.add_parser("list", help="list every receipt and customer delivery note").set_defaults(
        handler=cmd_receipts_list
    )
    show = receipts_sub.add_parser("show", help="print one customer delivery note")
    show.add_argument("reference", help="delivery note reference like CDN-0001")
    show.set_defaults(handler=cmd_receipts_show)

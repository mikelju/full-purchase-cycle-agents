"""Command line of the sales side: `sales invoice`, `scan`, `responses`, `approve`, `reject`, `list` and `show`."""

import re
import sys
from pathlib import Path

from purchase_cycle import config, customer_responses, db, sales
from purchase_cycle.claims import DecisionRefused
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import RESPONSE_DRAFTING, MissingRecording, ModelClient

REFERENCE = re.compile(r"SINV-(\d{1,9})", re.IGNORECASE)
INCIDENT_TYPES = ("missing", "extra", "damaged")


def _seeded(path):
    conn = db.connect(path)
    db.seed(conn)
    return conn


def _money(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def _number(value) -> str:
    return "-" if value is None else f"{value:g}"


def _invoice_id(reference: str) -> int | None:
    found = REFERENCE.fullmatch(reference.strip())
    return int(found.group(1)) if found else None


def _known_invoice(conn, reference: str) -> int | None:
    """The invoice id of a `SINV-NNNN` reference, or None after printing the error."""
    invoice_id = _invoice_id(reference)
    if invoice_id is None:
        print(f"Error: '{reference}' is not an invoice reference like SINV-0001", file=sys.stderr)
        return None
    if conn.execute("SELECT 1 FROM customer_invoices WHERE id = ?", (invoice_id,)).fetchone() is None:
        print(f"Error: no customer invoice {reference}", file=sys.stderr)
        return None
    return invoice_id


def cmd_sales_invoice(args) -> int:
    conn = _seeded(args.db)
    try:
        issued = sales.invoice(conn, args.invoices_dir)
    finally:
        conn.close()
    for item in issued:
        print(f"  issued {item['number']}  CDN-{item['note_id']:04d}")
    if not issued:
        print("nothing changed")
    return 0


def cmd_sales_scan(args) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Error: {args.folder} is not a folder", file=sys.stderr)
        return 1
    _seeded(args.db).close()
    checkpointer = sqlite_checkpointer(args.checkpoints)
    try:
        state = sales.run_sales(args.db, args.invoices_dir, folder, checkpointer)
    finally:
        checkpointer.conn.close()
    invoiced = state.get("invoiced", [])
    linked = state.get("linked", [])
    reconciled = state.get("reconciled", [])
    corrections = state.get("corrections", [])
    for item in invoiced:
        print(f"  issued {item['number']}")
    for item in linked:
        extra = f"  reason: {item['reason']}" if item.get("reason") else ""
        print(f"  signed note {Path(item['file']).name}  {item['status']}{extra}")
    for item in reconciled:
        print(f"  reconciled SINV-{item['invoice_id']:04d}  {item['result']}")
    for item in corrections:
        print(f"  planned {item['number']}  {item['kind']}  SINV-{item['invoice_id']:04d}")
    if not (invoiced or linked or reconciled or corrections):
        print("nothing changed")
    return 0


def cmd_sales_responses(args) -> int:
    _seeded(args.db).close()
    client = ModelClient(args.mode, [], args.recordings, task=RESPONSE_DRAFTING)
    checkpointer = sqlite_checkpointer(args.checkpoints)
    try:
        drafted = customer_responses.draft_responses(args.db, checkpointer, client)
    except MissingRecording as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        checkpointer.conn.close()
    saved = client.save_recordings()
    print(
        f"mode={args.mode}  responses drafted: {len(drafted)}"
        if drafted
        else f"mode={args.mode}  nothing to be drafted"
    )
    for item in drafted:
        facts, response = item["facts"], item["response"]
        print(
            f"{facts['invoice']}  to {response['recipient']}  subject: {response['subject']}  "
            f"source={response['draft_source']}  {response['status']}"
        )
        print(
            f"  customer={facts['customer']}  delivery note={facts['delivery_note']}  net total={_money(facts['net_total'])} EUR"
        )
        for row in facts["incidents"]:
            print(f"  incident {row['sku']}  {row['product']}  {row['type']}  units={row['units']}")
        for doc in facts["documents"]:
            print(f"  document {doc['number']}  {doc['kind']}  total={_money(doc['total'])} EUR")
        print("  draft:")
        print("\n".join(f"    {line}" for line in response["draft_body"].splitlines()))
    if client.calls:
        print(f"model calls: {client.calls}")
    if saved:
        print(f"recordings saved: {saved}")
    return 0


def _decision(args, decide) -> int:
    conn = _seeded(args.db)
    try:
        invoice_id = _known_invoice(conn, args.reference)
        number = f"SINV-{invoice_id:04d}" if invoice_id else None
    finally:
        conn.close()
    if invoice_id is None:
        return 1
    try:
        done = decide(invoice_id)
    except DecisionRefused as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if done["status"] == customer_responses.SENT:
        how = "edited" if done["edited"] else "as drafted"
        print(f"{number} response sent ({how})  to {done['recipient']}  file={done['outbox_file']}")
    else:
        print(f"{number} response rejected  no email")
    return 0


def cmd_sales_approve(args) -> int:
    body = None
    if args.body_file is not None:
        try:
            body = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as error:
            print(f"Error: cannot read {args.body_file}: {error}", file=sys.stderr)
            return 1
    return _decision(
        args,
        lambda invoice_id: customer_responses.approve_response(
            args.db, sqlite_checkpointer(args.checkpoints), args.invoices_dir, args.outbox, invoice_id, body
        ),
    )


def cmd_sales_reject(args) -> int:
    return _decision(
        args,
        lambda invoice_id: customer_responses.reject_response(
            args.db, sqlite_checkpointer(args.checkpoints), invoice_id
        ),
    )


def cmd_sales_list(args) -> int:
    conn = _seeded(args.db)
    try:
        rows = conn.execute(
            "SELECT i.id, i.number, i.delivery_note_id, i.net_total, r.result, r.signed_note_id, p.status "
            "FROM customer_invoices i LEFT JOIN sales_reconciliations r ON r.invoice_id = i.id "
            "LEFT JOIN customer_responses p ON p.reconciliation_id = r.id ORDER BY i.id"
        ).fetchall()
        print(f"invoices: {len(rows)}")
        for row in rows:
            counts = {kind: 0 for kind in INCIDENT_TYPES}
            if row["signed_note_id"] is not None:
                for kind, units in conn.execute(
                    "SELECT type, SUM(units) FROM signed_note_incidents WHERE signed_note_id = ? GROUP BY type",
                    (row["signed_note_id"],),
                ):
                    counts[kind] = units
            incidents = " ".join(f"{kind}={units}" for kind, units in counts.items())
            print(
                f"  {row['number']}  CDN-{row['delivery_note_id']:04d}  net total={_money(row['net_total'])} EUR  "
                f"result={row['result'] or 'not reconciled'}  incidents: {incidents}  "
                f"response={row['status'] or '-'}"
            )
    finally:
        conn.close()
    return 0


def cmd_sales_show(args) -> int:
    conn = _seeded(args.db)
    try:
        invoice_id = _known_invoice(conn, args.reference)
        if invoice_id is None:
            return 1
        invoice = conn.execute(
            "SELECT i.*, c.name FROM customer_invoices i JOIN customers c ON c.code = i.customer_code WHERE i.id = ?",
            (invoice_id,),
        ).fetchone()
        print(
            f"{invoice['number']}  CDN-{invoice['delivery_note_id']:04d}  ORD-{invoice['order_id']:04d}  "
            f"customer={invoice['customer_code']} ({invoice['name']})  issued={invoice['issued_at']}  "
            f"net total={_money(invoice['net_total'])} EUR"
        )
        for line in conn.execute(
            "SELECT * FROM customer_invoice_lines WHERE invoice_id = ? ORDER BY line_no", (invoice_id,)
        ):
            print(
                f"  line {line['line_no']}  {line['sku']}  quantity={line['quantity']}  "
                f"unit price={_money(line['unit_price'])}  total={_money(line['line_total'])}"
            )
        rec = conn.execute("SELECT * FROM sales_reconciliations WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if rec is None:
            print("result: not reconciled")
            return 0
        print(f"result: {rec['result']}  reconciled={rec['reconciled_at']}")
        if rec["signed_note_id"] is not None:
            signed = conn.execute(
                "SELECT * FROM signed_delivery_notes WHERE id = ?", (rec["signed_note_id"],)
            ).fetchone()
            print(
                f"signed note: signer={signed['signer']}  date={signed['signing_date']}  file={signed['source_file']}"
            )
            for inc in conn.execute(
                "SELECT n.sku, p.name, n.type, n.units FROM signed_note_incidents n JOIN products p ON p.sku = n.sku "
                "WHERE n.signed_note_id = ? ORDER BY n.line_no",
                (rec["signed_note_id"],),
            ):
                print(f"  incident {inc['sku']}  {inc['name']}  {inc['type']}  units={inc['units']}")
        for row in conn.execute(
            "SELECT * FROM sales_discrepancies WHERE reconciliation_id = ? ORDER BY id", (rec["id"],)
        ):
            print(
                f"  difference {row['type']}  incident={row['incident']}  sku={row['sku'] or '-'}  "
                f"expected={_number(row['expected'])}  found={_number(row['found'])}  "
                f"per unit={_money(row['unit_amount'])}  amount={_money(row['amount'])}"
            )
        for doc in conn.execute("SELECT * FROM customer_corrections WHERE invoice_id = ? ORDER BY id", (invoice_id,)):
            print(
                f"document {doc['number']}  {doc['kind']}  total={_money(doc['total'])} EUR  "
                f"file={doc['document_file'] or '-'}"
            )
            for line in conn.execute(
                "SELECT * FROM customer_correction_lines WHERE correction_id = ? ORDER BY line_no", (doc["id"],)
            ):
                print(
                    f"  line {line['line_no']}  {line['sku']}  quantity={line['quantity']}  "
                    f"unit price={_money(line['unit_price'])}  total={_money(line['line_total'])}"
                )
        response = customer_responses.response_of(conn, invoice_id)
        if response is None:
            print("response: none")
            return 0
        print(
            f"response: {response['status']}  to {response['recipient']}  subject: {response['subject']}  "
            f"source={response['draft_source']}  edited={'yes' if response['edited'] else 'no'}  "
            f"drafted={response['drafted_at']}  decided={response['decided_at'] or '-'}  "
            f"file={response['outbox_file'] or '-'}"
        )
        print("  draft:")
        print("\n".join(f"    {line}" for line in response["draft_body"].splitlines()))
        if response["final_body"] is not None and response["final_body"] != response["draft_body"]:
            print("  final:")
            print("\n".join(f"    {line}" for line in response["final_body"].splitlines()))
    finally:
        conn.close()
    return 0


def add_sales_commands(sub) -> None:
    parser = sub.add_parser("sales", help="invoice delivered orders, read signed notes and answer the customer")
    sales_sub = parser.add_subparsers(dest="sales_command", required=True)
    invoices = ("--invoices-dir", str(config.CUSTOMER_INVOICES_DIR), "folder for the invoice and document files")
    checkpoints = ("--checkpoints", str(config.default_checkpoint_path()), "SQLite checkpoints file")

    def add(command, name, help_text, *options):
        item = command.add_parser(name, help=help_text)
        for option, default, text in options:
            item.add_argument(option, default=default, help=text)
        return item

    add(sales_sub, "invoice", "issue the missing customer invoices", invoices).set_defaults(handler=cmd_sales_invoice)
    scan = add(sales_sub, "scan", "read the signed notes of a folder and reconcile", invoices, checkpoints)
    scan.add_argument("folder", help="folder of signed delivery note .txt files")
    scan.set_defaults(handler=cmd_sales_scan)
    responses = add(
        sales_sub,
        "responses",
        "draft one response per invoice with discrepancies",
        checkpoints,
        ("--recordings", None, "recordings file for the response drafting"),
    )
    responses.add_argument("--mode", choices=config.MODES, default="replay")
    responses.set_defaults(handler=cmd_sales_responses)
    approve = add(
        sales_sub,
        "approve",
        "send the response as drafted, or with --body-file",
        invoices,
        checkpoints,
        ("--body-file", None, "text file that replaces the drafted body"),
        ("--outbox", str(config.CUSTOMER_OUTBOX), "folder for the customer emails"),
    )
    approve.add_argument("reference", help="invoice reference like SINV-0001")
    approve.set_defaults(handler=cmd_sales_approve)
    reject = add(sales_sub, "reject", "close a response with no email", checkpoints)
    reject.add_argument("reference", help="invoice reference like SINV-0001")
    reject.set_defaults(handler=cmd_sales_reject)
    add(sales_sub, "list", "list every invoice with its result, incidents and response").set_defaults(
        handler=cmd_sales_list
    )
    show = add(sales_sub, "show", "show one invoice with incidents, differences, documents and response")
    show.add_argument("reference", help="invoice reference like SINV-0001")
    show.set_defaults(handler=cmd_sales_show)

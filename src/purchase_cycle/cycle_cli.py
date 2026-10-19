"""Command line of the orchestrator: `cycle run`, one pass over the whole purchase cycle."""

import sys

from purchase_cycle import config, cycle
from purchase_cycle.llm import MissingRecording, ModelClient

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

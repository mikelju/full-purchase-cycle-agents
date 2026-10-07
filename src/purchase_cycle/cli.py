"""Command line: seed the database and run the demo graphs."""

import argparse
import json
import sys
import uuid

from dotenv import load_dotenv

from purchase_cycle import config, db
from purchase_cycle.graph import build_graph, sqlite_checkpointer
from purchase_cycle.llm import MATCHING, InvalidModelOutput, MissingRecording, ModelClient
from purchase_cycle.web_form import build_web_form_graph

DEMO_SENTENCE = "Hi Laura, could you send us 40 boxes of powder-free nitrile gloves, size M? Thanks, Begona"
DEMO_SUBMISSION = config.ROOT / "examples" / "web_form_submission.json"


def cmd_seed(args) -> int:
    conn = db.connect(args.db)
    counts = db.seed(conn)
    conn.close()
    print(f"Seeded {args.db}")
    for table, count in counts.items():
        print(f"  {table:<12} {count}")
    return 0


def cmd_demo(args) -> int:
    conn = db.connect(args.db)
    db.seed(conn)
    client = ModelClient(args.mode, db.catalog_rows(conn))
    conn.close()
    graph = build_graph(client, args.db, checkpointer=sqlite_checkpointer(args.checkpoints))
    thread_id = args.thread_id or uuid.uuid4().hex[:12]
    run_config = {"configurable": {"thread_id": thread_id}, "run_name": "order_line_extraction"}
    graph_input = None if args.resume else {"sentence": args.sentence}
    if args.resume and not graph.get_state(run_config).values:
        print(f"Error: no checkpoint found for thread {thread_id}", file=sys.stderr)
        return 1
    try:
        state = graph.invoke(graph_input, run_config)
    except (MissingRecording, InvalidModelOutput) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    saved = client.save_recordings()
    line = state["extracted"]
    print(f"mode={args.mode}  thread_id={thread_id}")
    print(f"sentence: {state['sentence']}")
    print(f"extracted: sku={line['sku']}  quantity={line['quantity']}")
    product = state.get("product")
    if product:
        print(
            f"catalog: {product['name']}  ({product['sale_unit']}, {product['price_eur']:.2f} EUR, {product['on_hand']} in stock)"
        )
    else:
        print("catalog: no matching product")
    _print_usage(client)
    if saved:
        print(f"recordings saved: {saved}")
    return 0


def _print_usage(client) -> None:
    for usage in client.usage:
        print(
            f"tokens: uncached_input={usage['input_tokens']}  cache_read={usage['cache_read']}  cache_write={usage['cache_creation']}  output={usage['output_tokens']}"
        )


def cmd_web_form_demo(args) -> int:
    conn = db.connect(args.db)
    db.seed(conn)
    client = ModelClient(args.mode, db.catalog_rows(conn), task=MATCHING)
    conn.close()
    graph = build_web_form_graph(client, args.db, checkpointer=sqlite_checkpointer(args.checkpoints))
    thread_id = args.thread_id or uuid.uuid4().hex[:12]
    run_config = {"configurable": {"thread_id": thread_id}, "run_name": "web_form_order"}
    if args.resume:
        if not graph.get_state(run_config).values:
            print(f"Error: no checkpoint found for thread {thread_id}", file=sys.stderr)
            return 1
        graph_input = None
    else:
        try:
            with open(args.submission, encoding="utf-8-sig") as fh:
                graph_input = {"submission": json.load(fh)}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            print(f"Error: cannot read submission file {args.submission}: {error}", file=sys.stderr)
            return 1
    try:
        state = graph.invoke(graph_input, run_config)
    except (MissingRecording, InvalidModelOutput) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        saved = client.save_recordings()
    source = "resumed from its last checkpoint" if args.resume else f"submission={args.submission}"
    print(f"mode={args.mode}  thread_id={thread_id}  {source}")
    if state["errors"]:
        print("submission rejected, nothing stored:")
        for error in state["errors"]:
            print(f"  {error}")
        return 1
    print("matching:")
    for n, line in enumerate(state["lines"], start=1):
        print(f'  {n}. "{line["product"]}" x {line["quantity"]} -> {line["sku"] or "no match"}  ({line["source"]})')
    order = state["order_id"]
    print(f"stored order: {order if order is not None else 'none (no line matched)'}")
    print("reply:")
    print(state["reply"])
    _print_usage(client)
    if saved:
        print(f"recordings saved: {saved}")
    return 0


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="purchase-cycle")
    parser.add_argument("--db", default=str(config.default_db_path()), help="business database path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="create and seed the business database")

    demo = sub.add_parser("demo", help="run the order line extraction graph on one sentence")
    demo.add_argument("sentence", nargs="?", default=DEMO_SENTENCE)
    demo.add_argument("--mode", choices=config.MODES, default="replay")
    demo.add_argument("--thread-id", help="checkpoint thread to start or resume")
    demo.add_argument("--resume", action="store_true", help="continue the thread from its last checkpoint")
    demo.add_argument("--checkpoints", default=str(config.default_checkpoint_path()))

    web = sub.add_parser("web-form-demo", help="run the web form order subgraph on a submission file")
    web.add_argument("submission", nargs="?", help=f"submission JSON file (default: {DEMO_SUBMISSION.name})")
    web.add_argument("--mode", choices=config.MODES, default="replay")
    web.add_argument("--thread-id", help="checkpoint thread to start or resume")
    web.add_argument("--resume", action="store_true", help="continue the thread from its last checkpoint")
    web.add_argument("--checkpoints", default=str(config.default_checkpoint_path()))

    from purchase_cycle.evaluation.cli import add_eval_commands

    add_eval_commands(sub)

    args = parser.parse_args(argv)
    if args.command == "demo" and args.resume and not args.thread_id:
        demo.error("--resume needs --thread-id of the thread to continue")
    if args.command == "web-form-demo" and args.resume and not args.thread_id:
        web.error("--resume needs --thread-id of the thread to continue")
    if args.command == "web-form-demo":
        if args.resume and args.submission:
            web.error("--resume continues the stored submission of the thread; do not pass a submission file")
        args.submission = args.submission or str(DEMO_SUBMISSION)
    config.configure_tracing(getattr(args, "mode", "replay"))
    handlers = {"seed": cmd_seed, "demo": cmd_demo, "web-form-demo": cmd_web_form_demo}
    return (handlers.get(args.command) or args.handler)(args)


if __name__ == "__main__":
    sys.exit(main())

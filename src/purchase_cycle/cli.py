"""Command line: seed the database and run the demo graphs."""

import argparse
import json
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command

from purchase_cycle import config, db
from purchase_cycle.clarification import ClarificationClients, InvalidAnswer, InvalidQuestion
from purchase_cycle.email_order import CHANNEL as EMAIL
from purchase_cycle.email_order import InvalidExtraction, build_email_order_graph
from purchase_cycle.graph import build_graph, sqlite_checkpointer
from purchase_cycle.llm import (
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    InvalidModelOutput,
    MissingRecording,
    ModelClient,
)
from purchase_cycle.web_form import CHANNEL as WEB_FORM
from purchase_cycle.web_form import build_web_form_graph

DEMO_SENTENCE = "Hi Laura, could you send us 40 boxes of powder-free nitrile gloves, size M? Thanks, Begona"
DEMO_SUBMISSION = config.ROOT / "examples" / "web_form_submission.json"
DEMO_EMAILS = config.ROOT / "examples" / "email_orders"
DEMO_EXCEPTIONS = config.ROOT / "examples" / "exceptions"
# The exception samples are detection test items, so their channel steps replay the detection recordings.
EXCEPTIONS_RECORDINGS = {
    MATCHING.name: config.CLARIFICATION_DETECTION_MATCHING_RECORDINGS_PATH,
    EMAIL_INTAKE.name: config.CLARIFICATION_DETECTION_INTAKE_RECORDINGS_PATH,
    EMAIL_EXTRACTION.name: config.CLARIFICATION_DETECTION_EXTRACTION_RECORDINGS_PATH,
}
EXCEPTION_SAMPLES = (
    (WEB_FORM, "web_form_submission.json", "web_form_answer.txt"),
    (EMAIL, "email_order.eml", "email_answer.txt"),
)


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


def cmd_email_demo(args) -> int:
    paths = sorted(Path(args.folder).glob("*.eml"))
    if not paths:
        print(f"Error: no .eml files in {args.folder}", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    db.seed(conn)
    catalog = db.catalog_rows(conn)
    conn.close()
    intake = ModelClient(args.mode, catalog, task=EMAIL_INTAKE)
    extraction = ModelClient(args.mode, catalog, task=EMAIL_EXTRACTION)
    graph = build_email_order_graph(intake, extraction, args.db, checkpointer=sqlite_checkpointer(args.checkpoints))
    run_id = uuid.uuid4().hex[:12]
    print(f"mode={args.mode}  folder={args.folder}  emails={len(paths)}")
    code = 0
    for path in paths:
        thread_id = f"{run_id}-{path.stem}"
        run_config = {"configurable": {"thread_id": thread_id}, "run_name": "email_order"}
        print()
        print(f"== {path.name}  thread_id={thread_id}")
        try:
            state = graph.invoke({"email_path": str(path)}, run_config)
        except (MissingRecording, InvalidModelOutput, InvalidExtraction) as error:
            print(f"Error: {error}", file=sys.stderr)
            code = 1
            continue
        except (
            Exception
        ) as error:  # any other failure stops this email only, for example a quantity SQLite cannot store
            print(f"Error: {path.name} failed: {type(error).__name__}: {error}", file=sys.stderr)
            code = 1
            continue
        if state["errors"]:
            print("email rejected, nothing stored:")
            for error in state["errors"]:
                print(f"  {error}")
            code = 1
            continue
        email = state["email"]
        print(f"from: {email['sender']}  subject: {email['subject']}")
        print(f"intake: {'order' if state['is_order'] else 'not an order'} - {state['reason']}")
        if not state["is_order"]:
            print("nothing extracted or stored")
            continue
        print("lines:")
        for n, line in enumerate(state["lines"], start=1):
            print(
                f'  {n}. "{line["source_text"]}" x {line["quantity"]} -> {line["sku"] or "no match"}  ({line["source"]})'
            )
        order = state["order_id"]
        print(f"stored order: {order if order is not None else 'none (no line matched)'}")
        print("reply:")
        print(state["reply"])
    saved = intake.save_recordings() + extraction.save_recordings()
    print()
    for client in (intake, extraction):
        _print_usage(client)
    if saved:
        print(f"recordings saved: {saved}")
    return code


def _clarify_graph(args, channel: str, recordings: dict | None = None, channel_mode: str | None = None):
    """The channel graph with the clarify step, on the same checkpoint file; returns it and its clarification clients.

    `recordings` maps a channel task name to its recordings file and `channel_mode` sets the mode of the channel
    steps (matching, intake, extraction); both default to the task files and the command mode.
    """
    recordings = recordings or {}
    channel_mode = channel_mode or args.mode
    conn = db.connect(args.db)
    try:
        catalog = db.catalog_rows(conn)
    finally:
        conn.close()
    clients = ClarificationClients(
        ModelClient(args.mode, catalog, task=CLARIFICATION_QUESTION),
        ModelClient(args.mode, catalog, task=CLARIFICATION_ANSWER),
    )
    checkpointer = sqlite_checkpointer(args.checkpoints)
    if channel == WEB_FORM:
        matcher = ModelClient(channel_mode, catalog, recordings.get(MATCHING.name), task=MATCHING)
        return build_web_form_graph(matcher, args.db, checkpointer, clarification=clients), clients
    intake = ModelClient(channel_mode, catalog, recordings.get(EMAIL_INTAKE.name), task=EMAIL_INTAKE)
    extraction = ModelClient(channel_mode, catalog, recordings.get(EMAIL_EXTRACTION.name), task=EMAIL_EXTRACTION)
    return build_email_order_graph(intake, extraction, args.db, checkpointer, clarification=clients), clients


def cmd_exceptions_demo(args) -> int:
    conn = db.connect(args.db)
    db.seed(conn)
    conn.close()
    # Record mode records the question only; the channel steps replay the answers already recorded for the samples.
    channel_mode = "replay" if args.mode == "record" else args.mode
    run_id = uuid.uuid4().hex[:12]
    print(f"mode={args.mode}  samples={DEMO_EXCEPTIONS}")
    code = saved = 0
    for channel, sample, answer in EXCEPTION_SAMPLES:
        path = DEMO_EXCEPTIONS / sample
        graph, clients = _clarify_graph(args, channel, EXCEPTIONS_RECORDINGS, channel_mode)
        thread_id = f"{run_id}-{path.stem}"
        run_config = {"configurable": {"thread_id": thread_id}, "run_name": f"{channel}_order"}
        print()
        print(f"== {path.name}  channel={channel}  thread_id={thread_id}")
        try:
            if channel == WEB_FORM:
                with open(path, encoding="utf-8-sig") as fh:
                    graph_input = {"submission": json.load(fh)}
            else:
                graph_input = {"email_path": str(path)}
            state = graph.invoke(graph_input, run_config)
        except (MissingRecording, InvalidModelOutput, InvalidExtraction, InvalidQuestion) as error:
            print(f"Error: {error}", file=sys.stderr)
            code = 1
            continue
        finally:
            saved += clients.question.save_recordings()
        if state.get("errors"):
            print("rejected, nothing stored:")
            for error in state["errors"]:
                print(f"  {error}")
            code = 1
            continue
        print("lines:")
        for n, line in enumerate(state["lines"], start=1):
            text = line.get("source_text") or line["product"]
            print(f'  {n}. "{text}" x {line["quantity"]} -> {line["sku"] or "no match"}')
        paused = state.get("__interrupt__")
        if not paused:
            print("no doubts found")
            print(f"stored order: {state['order_id']}")
            continue
        question = paused[0].value
        print("doubts:")
        for doubt in question["doubts"]:
            candidates = ", ".join(c["sku"] for c in doubt["candidates"])
            detail = f"  candidates: {candidates}" if candidates else ""
            print(
                f'  line {doubt["line_id"]}: {", ".join(doubt["types"])} - "{doubt["text"]}" x {doubt["quantity"]}{detail}'
            )
        print(f"question (round {question['round']}):")
        print(question["question"])
        print(f"paused, nothing stored; thread_id={thread_id}")
        print(f"answer with: purchase-cycle clarify answer {thread_id} --file examples/exceptions/{answer}")
        _print_usage(clients.question)
    if saved:
        print(f"recordings saved: {saved}")
    return code


def _pending_row(args) -> dict | None:
    """The pending clarification of the thread, or None after printing why it cannot be resumed."""
    conn = db.connect(args.db)
    try:
        row = db.get_clarification(conn, args.thread_id)
    finally:
        conn.close()
    if row is None:
        print(f"Error: no clarification found for thread {args.thread_id}", file=sys.stderr)
        return None
    if row["status"] != "pending":
        print(
            f"Error: thread {args.thread_id} is not pending (status {row['status']}); nothing changed", file=sys.stderr
        )
        return None
    return row


def _resume(args, received: dict) -> int:
    """Resume a pending thread with an answer or a close marker and print the outcome."""
    row = _pending_row(args)
    if row is None:
        return 1
    graph, clients = _clarify_graph(args, row["channel"])
    run_config = {"configurable": {"thread_id": args.thread_id}, "run_name": f"{row['channel']}_order"}
    if not graph.get_state(run_config).next:
        print(f"Error: no paused checkpoint found for thread {args.thread_id}", file=sys.stderr)
        return 1
    try:
        state = graph.invoke(Command(resume=received), run_config)
    except (MissingRecording, InvalidModelOutput, InvalidAnswer, InvalidQuestion, db.NotPending) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        saved = clients.question.save_recordings() + clients.answer.save_recordings()
    print(f"mode={args.mode}  thread_id={args.thread_id}  channel={row['channel']}  customer={row['customer_code']}")
    paused = state.get("__interrupt__")
    if paused and paused[0].value.get("rejected"):
        print(
            f"Error: {paused[0].value['rejected']}; nothing changed, the thread still waits for an answer",
            file=sys.stderr,
        )
        return 1
    resolutions = state.get("resolutions")
    if paused:
        resolutions = graph.get_state(run_config, subgraphs=True).tasks[0].state.values.get("resolutions")
    if "answer" in received:
        print("interpretation:")
        latest = {r["line_id"]: r for r in resolutions or []}
        for line_id, r in sorted(latest.items()):
            detail = f" {r['sku']} x {r['quantity']}" if r["action"] == "set" else ""
            print(f"  line {line_id}: {r['action']}{detail}")
    if paused:
        question = paused[0].value
        print(f"new question (round {question['round']}):")
        print(question["question"])
        print(f"waiting for the answer: purchase-cycle clarify answer {args.thread_id} --text ...")
    else:
        order = state["order_id"]
        print(f"stored order: {order if order is not None else 'none (no line to store)'}")
        print("reply:")
        print(state["reply"])
    for client in clients:
        _print_usage(client)
    if saved:
        print(f"recordings saved: {saved}")
    return 0


def cmd_clarify_answer(args) -> int:
    if args.file:
        try:
            with open(args.file, encoding="utf-8-sig") as fh:
                text = fh.read().strip()
        except (OSError, UnicodeDecodeError) as error:
            print(f"Error: cannot read answer file {args.file}: {error}", file=sys.stderr)
            return 1
    else:
        text = args.text.strip()
    if not text:
        print("Error: the answer is empty", file=sys.stderr)
        return 1
    return _resume(args, {"answer": text})


def cmd_clarify_close(args) -> int:
    return _resume(args, {"close": True})


def cmd_clarify_list(args) -> int:
    conn = db.connect(args.db)
    try:
        db.seed(conn)
        rows = db.pending_clarifications(conn)
    finally:
        conn.close()
    if not rows:
        print("no pending clarifications")
        return 0
    print(f"pending clarifications: {len(rows)}")
    for row in rows:
        hours, minutes = divmod(row["age_minutes"], 60)
        print(
            f"  {row['thread_id']}  channel={row['channel']}  customer={row['customer_code']}  "
            f"round={row['round']}  age={hours}h{minutes:02d}m"
        )
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

    email = sub.add_parser("email-demo", help="run the email order subgraph on a folder of .eml files")
    email.add_argument(
        "folder",
        nargs="?",
        default=str(DEMO_EMAILS),
        help=f"folder of .eml files (default: examples/{DEMO_EMAILS.name})",
    )
    email.add_argument("--mode", choices=config.MODES, default="replay")
    email.add_argument("--checkpoints", default=str(config.default_checkpoint_path()))

    exceptions = sub.add_parser(
        "exceptions-demo", help="run the exception samples, print their doubts and question, and stop"
    )
    exceptions.add_argument("--mode", choices=config.MODES, default="replay")
    exceptions.add_argument("--checkpoints", default=str(config.default_checkpoint_path()))
    exceptions.set_defaults(handler=cmd_exceptions_demo)

    clarify = sub.add_parser("clarify", help="answer, list or close the pending clarification questions")
    clarify_sub = clarify.add_subparsers(dest="clarify_command", required=True)
    answer = clarify_sub.add_parser("answer", help="resume a paused order with the customer answer")
    answer.add_argument("thread_id")
    given = answer.add_mutually_exclusive_group(required=True)
    given.add_argument("--text", help="the customer answer")
    given.add_argument("--file", help="text file holding the customer answer")
    clarify_sub.add_parser("list", help="list the paused orders waiting for an answer")
    close = clarify_sub.add_parser("close", help="close a paused order without an answer, storing its clear lines")
    close.add_argument("thread_id")
    for command, handler in ((answer, cmd_clarify_answer), (close, cmd_clarify_close)):
        command.add_argument("--mode", choices=config.MODES, default="replay")
        command.add_argument("--checkpoints", default=str(config.default_checkpoint_path()))
        command.set_defaults(handler=handler)
    clarify_sub.choices["list"].set_defaults(handler=cmd_clarify_list)

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
    handlers = {"seed": cmd_seed, "demo": cmd_demo, "web-form-demo": cmd_web_form_demo, "email-demo": cmd_email_demo}
    return (handlers.get(args.command) or args.handler)(args)


if __name__ == "__main__":
    sys.exit(main())

"""Local test page for the orders channels (change 006): `purchase-cycle ui`.

A small `http.server` bound to 127.0.0.1 serves one HTML page and a few JSON endpoints.
Each send writes one input in the channel's own format (web form JSON, `.eml`, WhatsApp message JSON) into its own
folder under `data/ui/inbox/` and runs it through `router.run_inbox`, as `purchase-cycle route` does.
The page runs on its own database, checkpoints and outbox under `data/ui/`, never on the business database.
Replay answers from the recordings of the sample inbox (`orders_demo.jsonl`); live calls the model.
LangSmith tracing is forced off in both modes.
"""

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command

from purchase_cycle import cli, config, db, router, studio
from purchase_cycle.clarification import ClarificationClients, InvalidAnswer, InvalidQuestion
from purchase_cycle.email_order import build_email_order_graph
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import (
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    WHATSAPP_EXTRACTION,
    WHATSAPP_INTAKE,
    InvalidModelOutput,
    MissingRecording,
)
from purchase_cycle.studio import StudioModelClient
from purchase_cycle.web_form import build_web_form_graph
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph, phone_digits

UI_DIR = config.DATA_DIR / "ui"
RECORDINGS_PATH = studio.RECORDINGS_PATH  # the sample inbox answers of every task, plus the Studio-only ones
HTML_PATH = Path(__file__).with_name("ui.html")
UI_MODES = ("live", "replay")
CHANNELS = ("web_form", "email", "whatsapp")
HOST = "127.0.0.1"
MAX_BODY = 100_000  # bytes of one request
MAX_TEXT = 4000  # characters of one email body, WhatsApp text or answer
MAX_LINES = 50
KNOWN_ANSWER_ERRORS = (
    MissingRecording,
    InvalidModelOutput,
    InvalidAnswer,
    InvalidQuestion,
    db.NotPending,
    db.SourceConflict,
)


class BadRequest(Exception):
    """A request the page can fix; the server answers it with a JSON error and the status."""

    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


class UiApp:
    """The database, graphs and conversation log behind the page; one request runs at a time."""

    def __init__(self, mode: str, root: Path | str = UI_DIR):
        if mode not in UI_MODES:
            raise ValueError(f"mode must be one of {', '.join(UI_MODES)}, not {mode!r}")
        self.mode = mode
        self.root = Path(root)
        self.db_path = self.root / "purchase_cycle.db"
        self.outbox = self.root / "outbox"
        self.inbox = self.root / "inbox"
        self.log_path = self.root / "conversations.jsonl"
        self.lock = threading.Lock()
        if mode == "live":
            load_dotenv()  # the key lives in .env
        config.configure_tracing("replay")  # forces tracing off, also in live mode
        conn = db.connect(self.db_path)
        try:
            # First use is a database without catalog, also one a failed first seed left behind.
            has_catalog = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'products'"
            ).fetchone()
            if not has_catalog or not db.catalog_rows(conn):
                db.seed(conn)
            catalog = db.catalog_rows(conn)
        finally:
            conn.close()
        self.graphs = self._build_graphs(catalog)

    def _build_graphs(self, catalog) -> dict:
        def client(task):
            return StudioModelClient(self.mode, catalog, RECORDINGS_PATH, task=task)

        checkpointer = sqlite_checkpointer(self.root / "checkpoints.db")
        clarification = ClarificationClients(client(CLARIFICATION_QUESTION), client(CLARIFICATION_ANSWER))
        return {
            "web_form": build_web_form_graph(
                client(MATCHING), self.db_path, checkpointer, clarification=clarification, recovery=True
            ),
            "email": build_email_order_graph(
                client(EMAIL_INTAKE),
                client(EMAIL_EXTRACTION),
                self.db_path,
                checkpointer,
                clarification=clarification,
                recovery=True,
            ),  # fmt: skip
            "whatsapp": build_whatsapp_order_graph(
                client(WHATSAPP_INTAKE),
                client(WHATSAPP_EXTRACTION),
                self.db_path,
                self.outbox,
                checkpointer,
                clarification=clarification,
            ),  # fmt: skip
        }

    # Reads ---------------------------------------------------------------------------------------------------------

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = db.connect(self.db_path)
        try:
            return [dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def info(self) -> dict:
        return {"mode": self.mode}

    def catalog(self) -> list[dict]:
        return self._query(
            "SELECT p.sku, p.name, p.family, p.sale_unit, p.price_eur, COALESCE(s.on_hand, 0) AS stock "
            "FROM products p LEFT JOIN stock s ON s.sku = p.sku ORDER BY p.sku"
        )

    def customers(self) -> list[dict]:
        return self._query("SELECT code, name, kind, city, contact_name, email, phone FROM customers ORDER BY code")

    def _customer(self, code) -> dict:
        if not isinstance(code, str) or not code.strip():
            raise BadRequest("choose a customer")
        found = self._query("SELECT code, name, contact_name, email, phone FROM customers WHERE code = ?", (code,))
        if not found:
            raise BadRequest(f"unknown customer '{code}'")
        return found[0]

    def conversations(self) -> list[dict]:
        """Every logged entry, oldest first; `pending` marks the last entry of a thread still waiting for an answer."""
        entries = []
        if self.log_path.exists():
            with self.log_path.open(encoding="utf-8") as fh:
                entries = [json.loads(line) for line in fh if line.strip()]
        pending = {
            row["thread_id"]: row for row in self._query("SELECT * FROM clarifications WHERE status = 'pending'")
        }
        last = {entry["thread_id"]: i for i, entry in enumerate(entries) if entry.get("thread_id")}
        for i, entry in enumerate(entries):
            row = pending.get(entry.get("thread_id"))
            entry["pending"] = bool(row) and last[entry["thread_id"]] == i and entry["status"] == "question"
            entry["can_answer"] = entry["pending"] and entry["channel"] != "whatsapp"
        return entries

    # Writes --------------------------------------------------------------------------------------------------------

    def _log(self, entry: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return entry

    def _entry(self, channel: str, customer: dict, sent: str, routed, thread_id, state, error) -> dict:
        state = state or {}
        paused = state.get("__interrupt__")
        if error is not None:
            status = "failed"
        elif routed.kind == router.REJECTED:
            status = "rejected"
        elif routed.kind == router.DUPLICATE:
            status = "duplicate"
        elif state.get("errors"):
            status = "rejected"
        elif paused and paused[0].value.get("rejected"):
            status = "answer_rejected"
        elif paused:
            status = "question"
        elif state.get("is_order") is False:
            status = "not_order"
        elif state.get("order_id") is not None:
            status = "stored"
        else:
            status = "no_line"
        question = paused[0].value if paused else {}
        return {
            "at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "channel": channel,
            "customer": customer["code"],
            "customer_name": customer["name"],
            "sent": sent,
            "route": routed.kind,
            "reason": routed.reason,
            "thread_id": thread_id,
            "status": status,
            "outcome": cli._demo_outcome(routed, state, error),
            "order_id": state.get("order_id"),
            "round": question.get("round"),
            "reply": cli._demo_reply(state),
        }

    def send(self, payload: dict) -> dict:
        """Write the input in the channel's format, route it and return the conversation entry."""
        channel = payload.get("channel")
        if channel not in CHANNELS:
            raise BadRequest(f"unknown channel '{channel}'; use one of {', '.join(CHANNELS)}")
        customer = self._customer(payload.get("customer"))
        send_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        folder = self.inbox / send_id
        if channel == "web_form":
            sent = self._write_web_form(folder, send_id, customer, payload)
        elif channel == "email":
            sent = self._write_email(folder, customer, payload)
        else:
            sent = self._write_whatsapp(folder, send_id, customer, payload)
        with self.lock:
            results = router.run_inbox(folder, self.graphs, self.db_path, send_id, self.outbox)
            result = results[0]
            entry = self._entry(
                channel, customer, sent, result["route"], result["thread_id"], result["state"], result["error"]
            )
            return self._log(entry)

    def _write_web_form(self, folder: Path, send_id: str, customer: dict, payload: dict) -> str:
        lines = payload.get("lines")
        if not isinstance(lines, list) or not lines:
            raise BadRequest("add at least one order line")
        if len(lines) > MAX_LINES:
            raise BadRequest(f"a form takes at most {MAX_LINES} lines")
        clean = []
        for n, line in enumerate(lines, 1):
            product = line.get("product") if isinstance(line, dict) else None
            quantity = line.get("quantity") if isinstance(line, dict) else None
            if not isinstance(product, str) or not product.strip() or len(product) > 200:
                raise BadRequest(f"line {n}: write the product")
            if not isinstance(quantity, int) or isinstance(quantity, bool) or not 0 < quantity <= 100_000:
                raise BadRequest(f"line {n}: the quantity must be a whole number above zero")
            clean.append({"product": product.strip(), "quantity": quantity})
        submission = {"submission_id": f"WF-UI-{send_id}", "customer_code": customer["code"], "lines": clean}
        folder.mkdir(parents=True)
        (folder / "web-form.json").write_text(json.dumps(submission, indent=2), encoding="utf-8")
        return "; ".join(f"{line['quantity']} x {line['product']}" for line in clean)

    def _write_email(self, folder: Path, customer: dict, payload: dict) -> str:
        subject = payload.get("subject", "")
        body = payload.get("body")
        if not isinstance(subject, str) or "\n" in subject or "\r" in subject or len(subject) > 200:
            raise BadRequest("the subject must be one short line")
        body = _text(body, "write the email body")
        message = EmailMessage()
        message["From"] = formataddr((customer["contact_name"], customer["email"]))
        message["To"] = "orders@purchase-cycle.example"
        message["Subject"] = subject.strip()
        message["Date"] = formatdate(usegmt=True)
        message["Message-ID"] = make_msgid(domain="purchase-cycle.example")
        message.set_content(body)
        folder.mkdir(parents=True)
        (folder / "email.eml").write_bytes(message.as_bytes())
        return f"{subject.strip()}\n{body}" if subject.strip() else body

    def _write_whatsapp(self, folder: Path, send_id: str, customer: dict, payload: dict) -> str:
        message = {
            "message_id": f"wamid.UI-{send_id}",
            "from": phone_digits(customer["phone"]),
            "timestamp": str(int(time.time())),
        }
        if payload.get("type") == "image":  # a photo, which the channel answers as text only
            message |= {"type": "image", "image": {"id": f"media-UI-{send_id}", "mime_type": "image/jpeg"}}
            sent = "(a photo)"
        else:
            text = _text(payload.get("text"), "write the message text")
            message |= {"type": "text", "text": {"body": text}}
            sent = text
        folder.mkdir(parents=True)
        (folder / "whatsapp.json").write_text(json.dumps(message, indent=2), encoding="utf-8")
        return sent

    def answer(self, payload: dict) -> dict:
        """Resume a paused web form or email order with the customer's answer, as `clarify answer` does."""
        thread_id = payload.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise BadRequest("missing thread_id")
        text = _text(payload.get("text"), "write the answer")
        conn = db.connect(self.db_path)
        try:
            row = db.get_clarification(conn, thread_id)
        finally:
            conn.close()
        if row is None:
            raise BadRequest(f"no clarification found for thread {thread_id}", HTTPStatus.NOT_FOUND)
        if row["status"] != "pending":
            raise BadRequest(f"thread {thread_id} is not pending (status {row['status']})", HTTPStatus.CONFLICT)
        if row["channel"] == "whatsapp":
            raise BadRequest("a WhatsApp answer is a new message from the same customer; send it as a message")
        customer = self._customer(row["customer_code"])
        run_config = {"configurable": {"thread_id": thread_id}, "run_name": f"{row['channel']}_order"}
        with self.lock:
            graph = self.graphs[row["channel"]]
            if not graph.get_state(run_config).next:
                raise BadRequest(f"no paused checkpoint found for thread {thread_id}", HTTPStatus.CONFLICT)
            try:
                state = graph.invoke(Command(resume={"answer": text}), run_config, durability="sync")
            except KNOWN_ANSWER_ERRORS as error:
                raise BadRequest(str(error), HTTPStatus.UNPROCESSABLE_ENTITY) from error
            entry = self._entry(row["channel"], customer, text, router.Route(row["channel"]), thread_id, state, None)
            entry["route"] = "answer"
            return self._log(entry)


def _text(value, missing: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BadRequest(missing)
    if len(value) > MAX_TEXT:
        raise BadRequest(f"the text is longer than {MAX_TEXT} characters")
    return value.strip()


class Handler(BaseHTTPRequestHandler):
    app: UiApp  # set by `make_server`
    server_version = "purchase-cycle-ui"

    def log_message(self, format, *args):  # quiet; the page shows what happened
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _host_ok(self) -> bool:
        # A page on another site must not reach this server through the browser (DNS rebinding).
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost")

    def do_GET(self):
        try:
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "only 127.0.0.1 and localhost are served")
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send(HTTPStatus.OK, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            routes = {
                "/api/info": self.app.info,
                "/api/catalog": self.app.catalog,
                "/api/customers": self.app.customers,
                "/api/conversations": self.app.conversations,
            }
            if path in routes:
                return self._json(HTTPStatus.OK, routes[path]())
            self._error(HTTPStatus.NOT_FOUND, f"nothing at {path}")
        except Exception as error:  # a failed request never stops the server
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(error).__name__}: {error}")

    def do_POST(self):
        try:
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "only 127.0.0.1 and localhost are served")
            path = self.path.split("?", 1)[0]
            actions = {"/api/send": self.app.send, "/api/answer": self.app.answer}
            if path not in actions:
                return self._error(HTTPStatus.NOT_FOUND, f"nothing at {path}")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self._error(HTTPStatus.BAD_REQUEST, f"send a JSON body of up to {MAX_BODY} bytes")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._error(HTTPStatus.BAD_REQUEST, "the request body is not valid JSON")
            if not isinstance(payload, dict):
                return self._error(HTTPStatus.BAD_REQUEST, "the request body must be a JSON object")
            self._json(HTTPStatus.OK, actions[path](payload))
        except BadRequest as error:
            self._error(error.status, str(error))
        except Exception as error:  # a failed graph run never stops the server
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(error).__name__}: {error}")


def make_server(mode: str, port: int = 8765, root: Path | str = UI_DIR) -> HTTPServer:
    """The server bound to 127.0.0.1 on `port` (0 picks a free one), with its app ready; `serve_forever` runs it."""
    app = UiApp(mode, root)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = HTTPServer((HOST, port), handler)
    server.app = app
    return server


def serve(mode: str, port: int) -> int:
    server = make_server(mode, port)
    url = f"http://{HOST}:{server.server_port}/"
    print(f"mode={mode}  page={url}  data={server.app.root}")
    print("LangSmith tracing is off. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0

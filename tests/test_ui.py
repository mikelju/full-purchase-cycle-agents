"""Change 006, C1 to C6: the local test page serves the catalog and runs sent orders through the router."""

import email
import json
import socket
import threading
import urllib.error
import urllib.request
from email import policy
from pathlib import Path

import pytest

from conftest import make_email
from purchase_cycle import cli, config, db, router, ui
from purchase_cycle.clarification import detect
from purchase_cycle.email_order import read_email
from purchase_cycle.llm import EMAIL_EXTRACTION, EMAIL_INTAKE, build_system_prompt, recording_key
from purchase_cycle.whatsapp_order import phone_digits
from test_clarification_graph import (
    ANSWER_1,
    DOUBT_FORM,
    EMAIL_ANSWER,
    EMAIL_BODY,
    EMAIL_LINES,
    EMAIL_QUESTION,
    QUESTION_1,
    QUESTION_2,
    RESOLUTIONS_1,
    SENDER,
    _form_lines,
    _record_form,
    _record_round,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "examples" / "orders"


@pytest.fixture
def loopback_only(monkeypatch):
    """No key and no outgoing connection except to this machine, where the test server listens."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    real_connect = socket.socket.connect
    attempts = []

    def guard(sock, address, *args, **kwargs):
        if address[0] != "127.0.0.1":
            attempts.append(address)
            raise OSError("network blocked by test")
        return real_connect(sock, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guard)
    return attempts


@pytest.fixture
def server(tmp_path, loopback_only):
    httpd = ui.make_server("replay", port=0, root=tmp_path / "ui")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    httpd.base = f"http://127.0.0.1:{httpd.server_port}"
    httpd.root = tmp_path / "ui"
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    httpd.app.close()


def call(server, method: str, path: str, body=None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(server.base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def sample_inputs() -> list[tuple[str, dict]]:
    """The sample inbox as page payloads, in file name order, with the customer each item belongs to."""
    conn = db.connect(":memory:")
    db.seed(conn)
    by_phone = {phone_digits(r["phone"]): r["code"] for r in conn.execute("SELECT code, phone FROM customers")}
    by_email = {r["email"]: r["code"] for r in conn.execute("SELECT code, email FROM customers")}
    conn.close()
    items = []
    for path in sorted(SAMPLES.iterdir()):
        if path.is_dir():
            continue
        if path.suffix == ".eml":
            msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
            sender = email.utils.parseaddr(msg["From"])[1]
            body = msg.get_body(("plain",)).get_content().strip()
            items.append(
                (path.name, {"channel": "email", "customer": by_email[sender], "subject": msg["Subject"], "body": body})
            )
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "submission_id" in data:
            items.append(
                (path.name, {"channel": "web_form", "customer": data["customer_code"], "lines": data["lines"]})
            )
        else:
            payload = {"channel": "whatsapp", "customer": by_phone[data["from"]]}
            payload |= {"text": data["text"]["body"]} if data["type"] == "text" else {"type": data["type"]}
            items.append((path.name, payload))
    return items


def test_paths_are_apart_from_the_business_database():
    assert ui.UI_DIR == config.DATA_DIR / "ui"
    assert ui.UI_DIR / "purchase_cycle.db" != config.default_db_path()


def test_c1_binds_to_loopback_seeds_its_own_database_and_forces_tracing_off(tmp_path, loopback_only):
    httpd = ui.make_server("replay", port=0, root=tmp_path / "ui")
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()
    conn = db.connect(tmp_path / "ui" / "purchase_cycle.db")
    try:
        assert db.catalog_rows(conn)
    finally:
        conn.close()
    assert (tmp_path / "ui" / "checkpoints.db").exists()
    import os

    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_c1_the_cli_command_defaults_to_live_on_port_8765(monkeypatch):
    seen = {}
    monkeypatch.setattr(ui, "serve", lambda mode, port: seen.update(mode=mode, port=port) or 0)
    assert cli.main(["ui"]) == 0
    assert seen == {"mode": "live", "port": 8765}
    assert cli.main(["ui", "--mode", "replay", "--port", "9001"]) == 0
    assert seen == {"mode": "replay", "port": 9001}


def test_c1_live_mode_is_not_forced_to_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "load_dotenv", lambda: None)
    app = ui.UiApp("live", tmp_path / "ui")
    assert app.info() == {"mode": "live"}
    with pytest.raises(ValueError):
        ui.UiApp("record", tmp_path / "other")


def test_c2_catalog_and_customers_endpoints(server):
    status, products = call(server, "GET", "/api/catalog")
    assert status == 200 and len(products) > 20
    assert set(products[0]) == {"sku", "name", "family", "sale_unit", "price_eur", "stock"}
    assert all(isinstance(p["stock"], int) for p in products)
    status, customers = call(server, "GET", "/api/customers")
    assert status == 200
    assert {"code", "name", "email", "phone"} <= set(customers[0])
    assert customers[0]["email"].endswith("@example.com") and customers[0]["phone"].startswith("+34")


def test_c3_each_channel_writes_its_own_input_and_returns_the_answer(server):
    form = {"channel": "web_form", "customer": "CLI-001", "lines": [{"product": "nitrile gloves M", "quantity": 20}]}
    status, entry = call(server, "POST", "/api/send", form)
    assert status == 200
    assert entry["route"] == router.WEB_FORM and entry["status"] == "stored" and entry["order_id"] == 1
    assert entry["thread_id"].startswith("web_form-") and "stored order 1" in entry["outcome"]
    assert entry["reply"]
    status, entry = call(server, "POST", "/api/send", {"channel": "email", "customer": "CLI-002", "subject": "Order",
                                                       "body": "Hello,\nPlease send:\n- 8 packs of cohesive bandage 7.5 cm\n- 2 units of lumbar support belt\nThanks,\nIker Zubiri"})  # fmt: skip
    assert entry["route"] == router.EMAIL and entry["status"] == "stored" and entry["order_id"] == 2
    assert "iker.cli-002@example.com" in entry["reply"]
    status, entry = call(server, "POST", "/api/send", {"channel": "whatsapp", "customer": "CLI-003",
                                                       "text": "hi, pls send 5 boxes of paper tape 2.5 cm and 10 boxes of spot plasters thx"})  # fmt: skip
    assert entry["route"] == router.WHATSAPP_NEW and entry["status"] == "stored" and entry["order_id"] == 3
    # One folder per send, each holding one input in the channel's format.
    files = sorted(p.relative_to(server.root / "inbox").parts[1] for p in (server.root / "inbox").glob("*/*"))
    assert files == ["email.eml", "web-form.json", "whatsapp.json"]
    eml = next((server.root / "inbox").glob("*/email.eml")).read_bytes()
    assert b"From: Iker Zubiri <iker.cli-002@example.com>" in eml
    wa = json.loads(next((server.root / "inbox").glob("*/whatsapp.json")).read_text())
    assert wa["from"] == "34600102202" and wa["type"] == "text"
    assert len(call(server, "GET", "/api/conversations")[1]) == 3


def test_c4_whatsapp_question_is_answered_with_a_new_message(server):
    doubt = {"channel": "whatsapp", "customer": "CLI-004",
             "text": "hi, pls send 20 boxes of nitrile gloves and 6 units of sharps container 5 litres thx"}  # fmt: skip
    _, asked = call(server, "POST", "/api/send", doubt)
    assert asked["status"] == "question" and asked["reply"] and asked["round"] == 1
    conversations = call(server, "GET", "/api/conversations")[1]
    assert conversations[0]["pending"] is True and conversations[0]["can_answer"] is False
    status, error = call(server, "POST", "/api/answer", {"thread_id": asked["thread_id"], "text": "Size L please."})
    assert status == 400 and "new message" in error["error"]
    _, answered = call(server, "POST", "/api/send", {"channel": "whatsapp", "customer": "CLI-004", "text": "Size L please, all of them."})  # fmt: skip
    assert answered["route"] == router.WHATSAPP_ANSWER and answered["thread_id"] == asked["thread_id"]
    assert answered["status"] == "stored" and answered["order_id"] == 1
    assert call(server, "GET", "/api/conversations")[1][0]["pending"] is False


def _recorded_server(tmp_path, monkeypatch, loopback_only):
    """A replay server whose recordings hold the doubt scenes of the phase 04 tests (web form and email)."""
    root = tmp_path / "ui"
    conn = db.connect(root / "purchase_cycle.db")
    db.seed(conn)
    catalog = db.catalog_rows(conn)
    conn.close()

    def record(sentence, answer, task):
        return recorder(catalog, sentence, answer, task=task)

    state = {}

    def recorder(catalog, sentence, answer, case_id="TEST-1", task=None):
        key = recording_key(build_system_prompt(catalog, task), sentence, task)
        path = tmp_path / "recordings.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "case_id": case_id, "sentence": sentence, "answer": answer}) + chr(10))
        state["path"] = path
        return path

    _record_form(catalog, recorder)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    _record_round(catalog, recorder, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    _record_round(catalog, recorder, doubts[2:], QUESTION_2)
    sample = make_email(tmp_path / "doubt.eml", SENDER, "Order", EMAIL_BODY)
    text = read_email(sample, conn_for(root))["text"]
    recorder(catalog, text, {"is_order": True, "reason": "An order."}, task=EMAIL_INTAKE)
    recorder(catalog, text, {"lines": EMAIL_LINES}, task=EMAIL_EXTRACTION)
    resolutions = [{"line_id": 3, "action": "remove", "sku": None, "quantity": None}]
    _record_round(catalog, recorder, detect(EMAIL_LINES, catalog, "email"), EMAIL_QUESTION, EMAIL_ANSWER, resolutions)
    monkeypatch.setattr(ui, "RECORDINGS_PATH", state["path"])
    httpd = ui.make_server("replay", port=0, root=root)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    httpd.base = f"http://127.0.0.1:{httpd.server_port}"
    httpd.root = root
    return httpd


def conn_for(root):
    return db.connect(root / "purchase_cycle.db")


def test_c4_web_form_and_email_questions_are_answered_through_the_answer_endpoint(tmp_path, monkeypatch, loopback_only):
    httpd = _recorded_server(tmp_path, monkeypatch, loopback_only)
    try:
        form = {"channel": "web_form", "customer": "CLI-002", "lines": DOUBT_FORM["lines"]}
        _, asked = call(httpd, "POST", "/api/send", form)
        assert asked["status"] == "question" and asked["reply"] == QUESTION_1
        listed = call(httpd, "GET", "/api/conversations")[1]
        assert listed[0]["pending"] is True and listed[0]["can_answer"] is True
        status, again = call(httpd, "POST", "/api/answer", {"thread_id": asked["thread_id"], "text": ANSWER_1})
        assert status == 200 and again["route"] == "answer" and again["thread_id"] == asked["thread_id"]
        assert again["status"] == "question" and again["round"] == 2 and again["reply"] == QUESTION_2
        listed = call(httpd, "GET", "/api/conversations")[1]
        assert [e["pending"] for e in listed] == [False, True]  # only the last entry of a thread takes the answer
        email = {"channel": "email", "customer": "CLI-002", "subject": "Order", "body": EMAIL_BODY}
        _, asked = call(httpd, "POST", "/api/send", email)
        assert asked["status"] == "question" and asked["reply"] == EMAIL_QUESTION
        status, done = call(httpd, "POST", "/api/answer", {"thread_id": asked["thread_id"], "text": EMAIL_ANSWER})
        assert status == 200 and done["status"] == "stored" and done["order_id"] == 1
        assert "2 hospital beds" in done["reply"]
        status, error = call(httpd, "POST", "/api/answer", {"thread_id": asked["thread_id"], "text": "again"})
        assert status == 409 and "not pending" in error["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.app.close()


def test_c5_the_sample_texts_give_the_same_outcomes_as_route(server, tmp_path):
    # `purchase-cycle orders-demo` on the sample folder (run_inbox with its recordings), on a separate database.
    other = ui.UiApp("replay", tmp_path / "route")
    expected = {r["item"]: cli._demo_outcome(r["route"], r["state"] or {}, r["error"])
                for r in router.run_inbox(SAMPLES, other.graphs, other.db_path, "ref", tmp_path / "route" / "ref-outbox")}  # fmt: skip
    got = {}
    for name, payload in sample_inputs():
        if name == "06-whatsapp-order-again.json":
            continue  # a re-delivery of one message id; the page gives every send a new id
        status, entry = call(server, "POST", "/api/send", payload)
        assert status == 200, entry
        got[name] = entry
    assert [n for n, _ in sample_inputs() if n != "06-whatsapp-order-again.json"] == list(got)
    for name, entry in got.items():
        assert entry["outcome"] == expected[name], name
    assert got["04-whatsapp-doubt.json"]["status"] == "question"
    assert got["07-whatsapp-answer.json"]["route"] == router.WHATSAPP_ANSWER
    assert got["07-whatsapp-answer.json"]["status"] == "stored"
    assert got["05-whatsapp-photo.json"]["status"] == "rejected"
    assert (
        "only read text" in got["05-whatsapp-photo.json"]["reply"] or "text" in got["05-whatsapp-photo.json"]["reply"]
    )
    assert [e["status"] for e in got.values()].count("stored") == 4


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"channel": "fax", "customer": "CLI-001", "text": "x"}, "unknown channel"),
        ({"customer": "CLI-001", "text": "x"}, "unknown channel"),
        ({"channel": "whatsapp", "customer": "NOPE", "text": "x"}, "unknown customer"),
        ({"channel": "whatsapp", "text": "x"}, "choose a customer"),
        ({"channel": "whatsapp", "customer": "CLI-001", "text": "   "}, "message text"),
        ({"channel": "email", "customer": "CLI-001", "subject": "s", "body": ""}, "email body"),
        ({"channel": "email", "customer": "CLI-001", "subject": "a\nb", "body": "x"}, "subject"),
        ({"channel": "web_form", "customer": "CLI-001", "lines": []}, "at least one"),
        ({"channel": "web_form", "customer": "CLI-001", "lines": [{"product": "", "quantity": 1}]}, "line 1"),
        ({"channel": "web_form", "customer": "CLI-001", "lines": [{"product": "x", "quantity": 0}]}, "line 1"),
        ({"channel": "web_form", "customer": "CLI-001", "lines": ["x"]}, "line 1"),
    ],
)
def test_c6_malformed_sends_return_json_errors(server, payload, message):
    status, body = call(server, "POST", "/api/send", payload)
    assert status == 400 and message in body["error"]
    assert not (server.root / "inbox").exists() or not any((server.root / "inbox").iterdir())


def test_c6_malformed_requests_and_answers_return_json_errors(server):
    assert call(server, "POST", "/api/send", raw=b"{not json")[0] == 400
    assert call(server, "POST", "/api/send", raw=b"[1]")[0] == 400
    assert call(server, "POST", "/api/send", raw=b"")[0] == 400
    assert call(server, "POST", "/api/nothing", {"a": 1})[0] == 404
    assert call(server, "GET", "/api/nothing")[0] == 404
    status, body = call(server, "POST", "/api/answer", {"text": "x"})
    assert status == 400 and "thread_id" in body["error"]
    status, body = call(server, "POST", "/api/answer", {"thread_id": "web_form-x-y"})
    assert status == 400 and "answer" in body["error"]
    status, body = call(server, "POST", "/api/answer", {"thread_id": "web_form-x-y", "text": "x"})
    assert status == 404 and "no clarification" in body["error"]


def test_c6_a_failed_graph_run_shows_its_error_and_the_server_keeps_running(server):
    # Free text that no recording covers: replay fails the run with MissingRecording.
    status, entry = call(server, "POST", "/api/send", {"channel": "whatsapp", "customer": "CLI-001", "text": "send me a hundred crutches please"})  # fmt: skip
    assert status == 200 and entry["status"] == "failed" and "MissingRecording" in entry["outcome"]
    status, entry = call(server, "POST", "/api/send", {"channel": "whatsapp", "customer": "CLI-003",
                                                       "text": "hi, pls send 5 boxes of paper tape 2.5 cm and 10 boxes of spot plasters thx"})  # fmt: skip
    assert status == 200 and entry["status"] == "stored"


def test_only_loopback_hosts_are_served(server):
    request = urllib.request.Request(server.base + "/api/info", headers={"Host": "evil.example"})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403


def test_c2_the_server_serves_the_page_with_its_controls(server):
    with urllib.request.urlopen(server.base + "/") as response:
        assert response.status == 200 and response.headers["Content-Type"].startswith("text/html")
        page = response.read().decode("utf-8")
    for element in (
        'id="filter"',
        'id="channel"',
        'id="customer"',
        'id="conversations"',
        '<datalist id="products">',
        'id="subject"',
        'id="text"',
    ):
        assert element in page  # fmt: skip
    assert "cdn" not in page.lower() and "http://" not in page and "https://" not in page


def test_the_page_is_part_of_the_package_build(tmp_path):
    import subprocess
    import zipfile

    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        assert "purchase_cycle/ui.html" in wheel.namelist()


def test_review_a_rejected_answer_leaves_the_thread_answerable(tmp_path, monkeypatch, loopback_only):
    httpd = _recorded_server(tmp_path, monkeypatch, loopback_only)
    try:
        form = {"channel": "web_form", "customer": "CLI-002", "lines": DOUBT_FORM["lines"]}
        _, asked = call(httpd, "POST", "/api/send", form)
        status, rejected = call(httpd, "POST", "/api/answer", {"thread_id": asked["thread_id"], "text": "unrecorded"})
        assert status == 200 and rejected["status"] == "answer_rejected"
        last = call(httpd, "GET", "/api/conversations")[1][-1]
        assert last["pending"] is True and last["can_answer"] is True
        status, again = call(httpd, "POST", "/api/answer", {"thread_id": asked["thread_id"], "text": ANSWER_1})
        assert status == 200 and again["status"] == "question" and again["round"] == 2
        assert again["reply"] == QUESTION_2
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.app.close()


def test_review_a_whatsapp_rejected_answer_stays_pending(server):
    doubt = "hi, pls send 20 boxes of nitrile gloves and 6 units of sharps container 5 litres thx"
    call(server, "POST", "/api/send", {"channel": "whatsapp", "customer": "CLI-004", "text": doubt})
    _, second = call(server, "POST", "/api/send", {"channel": "whatsapp", "customer": "CLI-004", "text": "unrecorded"})
    last = call(server, "GET", "/api/conversations")[1][-1]
    assert second["status"] == "answer_rejected" and last["pending"] is True


def test_review_a_whatsapp_thread_takes_a_message_only_when_it_is_the_latest_pending_one(server):
    doubt = "hi, pls send 20 boxes of nitrile gloves and 6 units of sharps container 5 litres thx"
    _, asked = call(server, "POST", "/api/send", {"channel": "whatsapp", "customer": "CLI-004", "text": doubt})
    conn = db.connect(server.root / "purchase_cycle.db")
    with conn:
        conn.execute(
            "INSERT INTO clarifications (thread_id, channel, customer_code, question, round, status, updated_at) "
            "VALUES ('web_form-newer', 'web_form', 'CLI-004', 'q', 1, 'pending', datetime('now', '+1 minute'))"
        )
    conn.close()
    first = call(server, "GET", "/api/conversations")[1][0]
    assert first["thread_id"] == asked["thread_id"] and first["pending"] is False


def test_review_posts_from_other_origins_or_without_json_are_refused(server):
    body = json.dumps({"channel": "whatsapp", "customer": "CLI-001", "text": "x"}).encode()

    def post(headers):
        request = urllib.request.Request(server.base + "/api/send", data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status
        except urllib.error.HTTPError as error:
            assert "error" in json.loads(error.read())
            return error.code

    json_type = {"Content-Type": "application/json"}
    assert post(json_type | {"Origin": "http://evil.example"}) == 403
    assert post(json_type | {"Origin": f"http://127.0.0.1:{server.server_port + 1}"}) == 403
    assert post({"Content-Type": "text/plain"}) == 415
    assert post({}) == 415
    assert post(json_type | {"Origin": server.base}) == 200
    assert post({"Content-Type": "application/json; charset=utf-8"}) == 200
    assert post({"Content-Type": "application/json", "Origin": f"http://localhost:{server.server_port}"}) == 200


def test_review_the_server_answers_while_an_idle_connection_is_open(server):
    idle = socket.create_connection(("127.0.0.1", server.server_port))
    try:
        assert call(server, "GET", "/api/info")[0] == 200
    finally:
        idle.close()


def test_review_a_truncated_log_line_is_skipped(server):
    server.app.root.mkdir(parents=True, exist_ok=True)
    (server.root / "conversations.jsonl").write_text('{"channel": "web_form", "thre\n', encoding="utf-8")
    assert call(server, "GET", "/api/conversations") == (200, [])


def test_review_a_parked_run_shows_as_under_review(server):
    app = server.app
    customer = {"code": "CLI-001", "name": "Clinic"}
    route = router.Route(router.EMAIL)
    assert app._entry("email", customer, "x", route, "email-1", {}, TimeoutError("late"))["status"] == "failed"
    conn = db.connect(app.db_path)  # recovery parks the thread, then re-raises the original transient error
    db.park_failure(conn, "email-1", "email", "x", "extract", "TimeoutError: late")
    conn.close()
    assert app._entry("email", customer, "x", route, "email-1", {}, TimeoutError("late"))["status"] == "under_review"


def test_review_errors_show_the_class_not_the_text(server, monkeypatch):
    def boom(payload):
        raise RuntimeError("secret path C:/x")

    monkeypatch.setattr(server.app, "send", boom)
    request = urllib.request.Request(
        server.base + "/api/send", data=b"{}", method="POST", headers={"Content-Type": "application/json"}
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 500
    message = json.loads(error.value.read())["error"]
    assert "RuntimeError" in message and "secret" not in message


def test_review_port_out_of_range_is_a_friendly_error(capsys):
    assert ui.serve("replay", 70000) == 1
    assert "port" in capsys.readouterr().err


def _catalog(server) -> dict:
    return {p["sku"]: p for p in call(server, "GET", "/api/catalog")[1]}


def test_c10_a_stored_order_shows_its_lines_prices_amounts_and_total(server):
    form = {"channel": "web_form", "customer": "CLI-001", "lines": [{"product": "nitrile gloves M", "quantity": 20}]}
    status, entry = call(server, "POST", "/api/send", form)
    assert entry["status"] == "stored"
    catalog = _catalog(server)
    block = entry["order"]
    assert block["lines"] and all(set(line) == {"sku", "name", "quantity", "unit_price", "amount", "stock", "short"} for line in block["lines"])  # fmt: skip
    for line in block["lines"]:
        product = catalog[line["sku"]]
        assert line["name"] == product["name"] and line["unit_price"] == product["price_eur"]
        assert line["amount"] == round(line["quantity"] * product["price_eur"], 2)
        assert line["stock"] == product["stock"] and line["short"] is (line["quantity"] > product["stock"])
    assert sum(line["quantity"] for line in block["lines"]) == 20
    assert block["total"] == round(sum(line["amount"] for line in block["lines"]), 2)
    assert entry["unmatched"] == []
    stored = call(server, "GET", "/api/conversations")[1][0]
    assert stored["order"] == block  # the same values while nothing changed


def test_c10_a_quantity_above_the_stock_is_marked_short(server):
    conn = db.connect(server.app.db_path)
    conn.execute("UPDATE stock SET on_hand = 3")
    conn.commit()
    conn.close()
    form = {"channel": "web_form", "customer": "CLI-001", "lines": [{"product": "nitrile gloves M", "quantity": 20}]}
    entry = call(server, "POST", "/api/send", form)[1]
    assert entry["status"] == "stored"
    assert [line["short"] for line in entry["order"]["lines"]] == [True] and entry["order"]["lines"][0]["stock"] == 3


def test_c10_entries_without_a_stored_order_carry_null_block_and_empty_list(server):
    entry = call(server, "POST", "/api/send", {"channel": "whatsapp", "customer": "CLI-001", "text": "hello, are you open on Saturday?"})[1]  # fmt: skip
    assert entry["order_id"] is None and entry["order"] is None and entry["unmatched"] == []


def test_c10_unmatched_removed_and_unresolved_lines_are_listed(server):
    state = {
        "order_id": None,
        "lines": [
            {"product": "left-handed widget", "sku": None, "quantity": 2},
            {"source_text": "that blue thing", "sku": None, "quantity": 1},
            {"source_text": "gauze", "sku": "X", "quantity": 1},
        ],
        "removed": [{"text": "sterile swabs", "quantity": 4}],
        "unresolved": [{"text": "the big box", "quantity": 1}],
    }
    customer = {"code": "CLI-001", "name": "n"}
    entry = server.app._entry("email", customer, "s", router.Route(router.EMAIL), "t", state, None)
    assert entry["unmatched"] == [
        "left-handed widget x 2 (not recognised)",
        "that blue thing x 1 (not recognised)",
        "sterile swabs x 4 (removed)",
        "the big box x 1 (not resolved)",
    ]


def test_c10_old_log_lines_without_the_new_fields_still_read_back(server):
    old = {"at": "2026-10-01 10:00:00", "channel": "email", "customer": "CLI-001", "customer_name": "n", "sent": "s",
           "route": "email", "reason": None, "thread_id": "email-old", "status": "stored", "outcome": "o", "order_id": 1,
           "round": None, "reply": "r"}  # fmt: skip
    server.app.root.mkdir(parents=True, exist_ok=True)
    server.app.log_path.write_text(json.dumps(old) + "\n", encoding="utf-8")
    entries = call(server, "GET", "/api/conversations")[1]
    assert entries[0]["thread_id"] == "email-old" and entries[0]["order"] is None  # order 1 does not exist here
    with urllib.request.urlopen(server.base + "/") as response:
        page = response.read().decode("utf-8")
    assert "entry.order" in page and "entry.unmatched" in page and "short of stock" in page


# C11 and C12: the database view and the stock shortfall check ----------------------------------------------------------

DATABASE_TABLES = ["orders", "order_lines", "stock", "quote_requests", "quote_offers", "purchase_orders"]


def _dump(server) -> list[str]:
    conn = db.connect(server.app.db_path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _short_order(server, quantity: int = 20, stock: int = 3) -> dict:
    """Store a web form order for gloves whose quantity exceeds the stock; returns the conversation entry."""
    conn = db.connect(server.app.db_path)
    conn.execute("UPDATE stock SET on_hand = ?", (stock,))
    conn.commit()
    conn.close()
    form = {
        "channel": "web_form",
        "customer": "CLI-001",
        "lines": [{"product": "nitrile gloves M", "quantity": quantity}],
    }
    entry = call(server, "POST", "/api/send", form)[1]
    assert entry["status"] == "stored"
    return entry


def test_c11_database_view_returns_the_six_tables_and_never_writes(server):
    entry = _short_order(server)
    before = _dump(server)
    status, data = call(server, "GET", "/api/database")
    assert status == 200
    assert [t["name"] for t in data["tables"]] == DATABASE_TABLES
    tables = {t["name"]: t for t in data["tables"]}
    assert tables["orders"]["rows"][0]["id"] == entry["order_id"]
    assert tables["order_lines"]["columns"] == ["id", "order_id", "sku", "quantity"]
    assert tables["order_lines"]["rows"][0]["quantity"] == 20
    assert tables["stock"]["rows"] and all(row["on_hand"] == 3 for row in tables["stock"]["rows"])
    assert tables["quote_requests"]["rows"] == [] and tables["quote_offers"]["rows"] == []
    assert tables["purchase_orders"]["rows"] == []
    assert _dump(server) == before  # reading changed nothing


def test_c11_database_view_is_get_only_and_the_page_has_it(server):
    assert call(server, "POST", "/api/database", {})[0] == 404
    with urllib.request.urlopen(server.base + "/") as response:
        page = response.read().decode("utf-8")
    assert "/api/database" in page and "Database" in page


def test_c12_scan_starts_a_request_for_a_shortfall_and_writes_the_supplier_emails(server):
    _short_order(server, quantity=20, stock=3)
    status, result = call(server, "POST", "/api/quotes/scan", {})
    assert status == 200
    [short] = result["shortfalls"]
    assert set(short) == {"sku", "demand", "on_hand", "requested", "shortfall"}
    assert (short["demand"], short["on_hand"], short["requested"], short["shortfall"]) == (20, 3, 0, 17)
    [started] = result["started"]
    assert started["sku"] == short["sku"] and started["quantity"] == 17 and started["reference"] == "QR-0001"
    assert started["emails"], "one request email per supplier of the family"
    for mail in started["emails"]:
        assert (server.app.outbox / mail["file"]).exists()
        assert mail["file"].startswith("QR-0001-request-") and mail["to"] and "QR-0001" in mail["subject"]
    assert sorted(p.name for p in server.app.outbox.glob("QR-0001-request-*.eml")) == sorted(
        m["file"] for m in started["emails"]
    )
    rows = {t["name"]: t["rows"] for t in call(server, "GET", "/api/database")[1]["tables"]}
    assert [r["sku"] for r in rows["quote_requests"]] == [short["sku"]]
    config = {"configurable": {"thread_id": "quote-QR-0001", "checkpoint_ns": ""}}
    assert server.app.checkpointer.get_tuple(config) is not None  # the request thread left a checkpoint


def test_c12_a_second_scan_with_nothing_new_says_so(server):
    _short_order(server)
    first = call(server, "POST", "/api/quotes/scan", {})[1]
    files = sorted(p.name for p in server.app.outbox.iterdir())
    again = call(server, "POST", "/api/quotes/scan", {})[1]
    assert again["started"] == [] and again["shortfalls"] == [] and again["blocked"] == []
    assert "no new" in again["message"].lower() and first["message"] != again["message"]
    assert sorted(p.name for p in server.app.outbox.iterdir()) == files  # no email repeated


def test_c12_scan_with_no_shortfall_starts_nothing(server):
    result = call(server, "POST", "/api/quotes/scan", {})[1]  # no orders at all
    assert result["started"] == [] and result["shortfalls"] == []
    entry = call(server, "POST", "/api/send", {"channel": "web_form", "customer": "CLI-001", "lines": [{"product": "nitrile gloves M", "quantity": 1}]})[1]  # fmt: skip
    assert entry["status"] == "stored"  # the seeded stock covers one unit
    result = call(server, "POST", "/api/quotes/scan", {})[1]
    assert result["started"] == [] and "no new" in result["message"].lower()
    assert not server.app.outbox.exists() or not list(server.app.outbox.glob("QR-*"))


def test_c12_scan_never_calls_the_model_and_keeps_the_origin_checks(server, loopback_only):
    _short_order(server)
    server.app.graphs = {}  # an order graph, and so a model client, would fail on this
    status, result = call(server, "POST", "/api/quotes/scan", {})
    assert status == 200 and len(result["started"]) == 1
    assert loopback_only == []  # no outgoing connection was tried
    request = urllib.request.Request(server.base + "/api/quotes/scan", data=b"{}", method="POST", headers={"Content-Type": "application/json", "Origin": "http://evil.example"})  # fmt: skip
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request)
    assert refused.value.code == 403
    assert call(server, "GET", "/api/quotes/scan")[0] == 404


def test_c12_the_page_has_the_button(server):
    with urllib.request.urlopen(server.base + "/") as response:
        page = response.read().decode("utf-8")
    assert "Check stock shortfalls" in page and "/api/quotes/scan" in page


def test_c10_the_order_block_is_read_at_view_time(server):
    entry = _short_order(server, quantity=20, stock=3)
    assert entry["order"]["lines"][0]["stock"] == 3
    conn = db.connect(server.app.db_path)
    conn.execute("UPDATE stock SET on_hand = 50")
    conn.execute("UPDATE products SET price_eur = price_eur + 1")
    conn.commit()
    conn.close()
    [stored] = call(server, "GET", "/api/conversations")[1]
    line = stored["order"]["lines"][0]
    assert line["stock"] == 50 and line["short"] is False
    assert line["unit_price"] == entry["order"]["lines"][0]["unit_price"] + 1


def test_c10_duplicate_skus_in_one_order_are_compared_as_a_sum(server):
    conn = db.connect(server.app.db_path)
    conn.execute("UPDATE stock SET on_hand = 10")
    conn.commit()
    conn.close()
    form = {"channel": "web_form", "customer": "CLI-001", "lines": [{"product": "nitrile gloves M", "quantity": 6}, {"product": "nitrile gloves M", "quantity": 6}]}  # fmt: skip
    entry = call(server, "POST", "/api/send", form)[1]
    lines = entry["order"]["lines"]
    assert [line["quantity"] for line in lines] == [6, 6] and all(line["short"] for line in lines)


def test_review_unmatched_is_empty_while_a_question_is_pending(server):
    state = {
        "lines": [{"product": "left-handed widget", "sku": None, "quantity": 2}],
        "__interrupt__": [type("Interrupt", (), {"value": {"round": 1, "question": "which one?"}})()],
    }
    customer = {"code": "CLI-001", "name": "n"}
    entry = server.app._entry("email", customer, "s", router.Route(router.EMAIL), "t", state, None)
    assert entry["status"] == "question" and entry["unmatched"] == []


def test_c12_an_earlier_closed_request_counts_as_already_requested(server):
    _short_order(server, quantity=20, stock=3)
    call(server, "POST", "/api/quotes/scan", {})
    conn = db.connect(server.app.db_path)
    conn.execute(
        "UPDATE quote_requests SET status = 'no_offers'"
    )  # closed by hand: no supplier replies are simulated here
    conn.commit()
    conn.close()
    form = {"channel": "web_form", "customer": "CLI-001", "lines": [{"product": "nitrile gloves M", "quantity": 10}]}
    assert call(server, "POST", "/api/send", form)[1]["status"] == "stored"
    result = call(server, "POST", "/api/quotes/scan", {})[1]
    [short] = result["shortfalls"]
    assert (short["demand"], short["on_hand"], short["requested"], short["shortfall"]) == (30, 3, 17, 10)
    assert [r["reference"] for r in result["started"]] == ["QR-0002"] and result["started"][0]["quantity"] == 10


def test_c12_extra_demand_behind_a_waiting_request_is_reported_as_blocked(server):
    _short_order(server, quantity=20, stock=3)
    call(server, "POST", "/api/quotes/scan", {})  # QR-0001 stays waiting
    form = {"channel": "web_form", "customer": "CLI-001", "lines": [{"product": "nitrile gloves M", "quantity": 10}]}
    assert call(server, "POST", "/api/send", form)[1]["status"] == "stored"
    result = call(server, "POST", "/api/quotes/scan", {})[1]
    assert result["started"] == [] and result["shortfalls"] == []
    [blocked] = result["blocked"]
    assert set(blocked) == {"sku", "demand", "on_hand", "requested", "shortfall", "waiting_reference"}
    assert (blocked["demand"], blocked["on_hand"], blocked["requested"], blocked["shortfall"]) == (30, 3, 17, 10)
    assert blocked["waiting_reference"] == "QR-0001"
    assert "waiting" in result["message"] and "no sku is short" not in result["message"].lower()


def test_c12_stale_emails_of_another_sku_are_not_listed(server):
    _short_order(server)
    server.app.outbox.mkdir(parents=True, exist_ok=True)
    (server.app.outbox / "QR-0001-request-ZZZ.eml").write_text(
        "To: old@example.com\nSubject: Quote request QR-0001\n\nSKU: OTHER-SKU-1\n", encoding="utf-8"
    )  # left by an older database
    [started] = call(server, "POST", "/api/quotes/scan", {})[1]["started"]
    assert started["emails"] and "QR-0001-request-ZZZ.eml" not in [m["file"] for m in started["emails"]]


def test_c12_a_failing_scan_returns_a_json_500_with_the_class_only(server, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("secret detail C:/private/path")

    monkeypatch.setattr(ui.quotes, "scan", broken)
    status, body = call(server, "POST", "/api/quotes/scan", {})
    assert status == 500 and "RuntimeError" in body["error"] and "secret" not in body["error"]
    assert call(server, "GET", "/api/info")[0] == 200  # the server keeps answering


def test_c12_blocked_nets_only_requests_not_yet_received(server):
    _short_order(server, quantity=190, stock=0)
    conn = db.connect(server.app.db_path)
    sku = conn.execute("SELECT sku FROM order_lines LIMIT 1").fetchone()[0]
    for n, (quantity, status) in enumerate([(100, "ordered"), (140, "waiting")], 1):
        conn.execute(
            "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
            "VALUES (?, ?, ?, ?, 't', 't', 't')",
            (sku, quantity, f"th-{n}", status),
        )
    conn.execute(
        "INSERT INTO purchase_orders (request_id, supplier_code, sku, quantity, unit_price, currency, total, "
        "expected_delivery, issued_at, status) VALUES (1, (SELECT code FROM suppliers LIMIT 1), ?, 100, 1.0, 'EUR', "
        "1.0, 't', 't', 'issued')",
        (sku,),
    )
    note = conn.execute(
        "INSERT INTO supplier_documents (kind, number, purchase_order_id, source_file, content_hash, status, created_at) "
        "VALUES ('delivery_note', 'DN-1', 1, 'f.txt', 'h', 'linked', 't')"
    ).lastrowid
    conn.execute(
        "INSERT INTO goods_receipts (delivery_note_id, purchase_order_id, received_at, status) VALUES (?, 1, 't', 'received')",
        (note,),
    )
    conn.commit()
    [blocked] = ui.UiApp._blocked(conn)  # request 1 is received: only the 140 of request 2 is still on its way
    conn.close()
    assert (blocked["demand"], blocked["requested"], blocked["shortfall"]) == (190, 140, 50)

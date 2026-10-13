"""C5 to C8: the decision graph and the decide, approve and reject commands on a temporary database."""

from email import message_from_bytes

import pytest

from purchase_cycle import cli, db, quote_decision, sim_clock
from purchase_cycle.graph import sqlite_checkpointer
from test_quote_decision import GOOD, _client, _setup

BODIES = ["Full offer for 12 boxes.", "Partial offer for 8 boxes.", None]
PARTIAL = {**GOOD, "quantity": 8, "unit_price": 3.0, "lead_time_days": 2}
CHEAP_SUP_001 = {**GOOD, "lead_time_days": 3}  # 3 working days


class World:
    def __init__(self, tmp_path, bodies=BODIES, answers=None):
        self.tmp = tmp_path
        self.db_path, self.conn = _setup(tmp_path, bodies)
        answers = answers or [CHEAP_SUP_001, PARTIAL, None]
        _client(tmp_path, bodies, answers)  # writes offers.jsonl
        self.recordings = tmp_path / "offers.jsonl"
        self.checkpoints = tmp_path / "checkpoints.db"
        self.outbox = tmp_path / "supplier_outbox"

    def run(self, *args):
        base = ["--db", str(self.db_path), "quotes", args[0]]
        extra = list(args[1:])
        if args[0] == "decide":
            extra += ["--recordings", str(self.recordings)]
        if args[0] in ("approve",):
            extra += ["--outbox", str(self.outbox)]
        return cli.main([*base, *extra, "--checkpoints", str(self.checkpoints)])

    def status(self, request_id=1):
        return self.conn.execute("SELECT status FROM quote_requests WHERE id = ?", (request_id,)).fetchone()[0]

    def orders(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM purchase_orders ORDER BY id")]

    def emails(self):
        return sorted(self.outbox.glob("*.eml")) if self.outbox.exists() else []

    def paused_at(self, request_id=1):
        graph = quote_decision.build_quote_decision_graph(
            self.db_path, None, None, sqlite_checkpointer(self.checkpoints)
        )
        return graph.get_state(quote_decision._decision_config(request_id)).next

    def untouched(self):
        return {
            t: [tuple(r) for r in self.conn.execute(f"SELECT * FROM {t}")] for t in ("orders", "order_lines", "stock")
        }


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_decide_prints_the_offers_and_recommendation_and_pauses_in_approve(world, capsys):
    assert world.run("decide") == 0
    out = capsys.readouterr().out
    assert "decisions started: 1" in out and "QR-0001" in out
    assert "SUP-001  usable" in out and "SUP-002  usable" in out
    assert "recommended: SUP-001" in out and "reason: Recommended SUP-001" in out
    assert world.status() == "awaiting_approval"
    assert world.paused_at() == ("approve",)
    assert world.orders() == []


def test_decide_lists_no_offers_requests_for_a_person_and_writes_no_order(world, capsys):
    world.conn.execute(
        "INSERT INTO quote_requests (id, sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
        "VALUES (2, 'GLV-NIT-S', 5, 'quote-QR-0002', 'no_offers', 't', 't', 't')"
    )
    world.conn.commit()
    assert world.run("decide") == 0
    out = capsys.readouterr().out
    assert "waiting for a person (no offers, no order): QR-0002" in out
    assert world.status(2) == "no_offers" and world.orders() == []


def test_a_second_decide_starts_nothing_and_calls_no_model(world, capsys):
    assert world.run("decide") == 0
    world.recordings.unlink()  # a model call now would fail on the missing recording
    capsys.readouterr()
    assert world.run("decide") == 0
    assert "decisions started: 0" in capsys.readouterr().out
    assert world.status() == "awaiting_approval"


def test_an_unusable_offer_is_printed_with_its_reason(tmp_path, capsys):
    declined = {**GOOD, "offers": False, "quantity": None, "unit_price": None}
    world = World(tmp_path, answers=[GOOD, declined, None])
    assert world.run("decide") == 0
    assert "SUP-002  unusable (declined)" in capsys.readouterr().out


def test_approve_issues_one_order_and_one_email_and_leaves_stock_and_orders_alone(world, capsys):
    world.run("decide")
    sim_clock.advance(world.conn, days=3)  # Thursday 2026-10-15; 3 working days end on Tuesday 2026-10-20
    before = world.untouched()
    assert world.run("approve", "QR-0001") == 0
    (order,) = world.orders()
    assert (order["supplier_code"], order["sku"], order["quantity"], order["unit_price"]) == (
        "SUP-001",
        "GLV-NIT-XS",
        12,
        4.1,
    )
    assert (order["currency"], order["total"], order["status"]) == ("EUR", 49.2, "issued")
    assert (order["issued_at"], order["expected_delivery"]) == ("2026-10-15 09:00:00", "2026-10-20")
    assert world.status() == "ordered"
    assert world.untouched() == before
    (email,) = world.emails()
    assert email.name == "PO-0001-SUP-001.eml"
    message = message_from_bytes(email.read_bytes())
    assert "PO-0001" in message["Subject"] and "QR-0001" in message["Subject"]
    body = message.get_payload()
    for expected in ("GLV-NIT-XS", "Quantity: 12", "Unit price: 4.1000 EUR", "Total: 49.20 EUR", "2026-10-20"):
        assert expected in body
    assert "PO-0001" in capsys.readouterr().out


def test_choosing_a_partial_offer_orders_the_offered_quantity(world):
    world.run("decide")
    assert world.run("approve", "QR-0001", "--supplier", "SUP-002") == 0
    (order,) = world.orders()
    assert (order["supplier_code"], order["quantity"], order["unit_price"], order["total"]) == ("SUP-002", 8, 3.0, 24.0)
    assert len(world.emails()) == 1


def test_a_refused_choice_changes_nothing_and_keeps_the_request_waiting(tmp_path, capsys):
    declined = {**GOOD, "offers": False, "quantity": None, "unit_price": None}
    world = World(tmp_path, answers=[GOOD, declined, None])
    world.run("decide")
    capsys.readouterr()
    for supplier, reason in (
        ("SUP-003", "made no offer"),
        ("SUP-002", "not usable (declined)"),
        ("SUP-009", "made no offer"),
    ):
        assert world.run("approve", "QR-0001", "--supplier", supplier) == 1
        assert reason in capsys.readouterr().err
    assert world.orders() == [] and world.emails() == []
    assert world.status() == "awaiting_approval" and world.paused_at() == ("approve",)


def test_a_request_with_no_usable_offer_accepts_only_reject(tmp_path, capsys):
    declined = {**GOOD, "offers": False, "quantity": None, "unit_price": None}
    world = World(tmp_path, answers=[declined, declined, None])
    world.run("decide")
    out = capsys.readouterr().out
    assert "recommended: none" in out and world.status() == "awaiting_approval"
    assert world.run("approve", "QR-0001") == 1
    assert "no usable offer" in capsys.readouterr().err
    assert world.orders() == [] and world.emails() == [] and world.paused_at() == ("approve",)
    assert world.run("reject", "QR-0001") == 0
    assert world.status() == "rejected"


def test_reject_closes_the_request_with_no_order_and_no_email(world, capsys):
    world.run("decide")
    assert world.run("reject", "QR-0001") == 0
    assert world.status() == "rejected" and world.orders() == [] and world.emails() == []
    assert "rejected" in capsys.readouterr().out


def test_commands_on_a_request_that_is_not_awaiting_approval_fail_with_a_message(world, capsys):
    assert world.run("approve", "QR-0001") == 1  # still ready_for_decision
    assert "ready_for_decision" in capsys.readouterr().err
    assert world.run("approve", "QR-0009") == 1
    assert "unknown" in capsys.readouterr().err


def test_approve_twice_leaves_one_order_and_one_email(world, capsys):
    world.run("decide")
    assert world.run("approve", "QR-0001") == 0
    capsys.readouterr()
    assert world.run("approve", "QR-0001") == 1
    assert "ordered" in capsys.readouterr().err
    assert len(world.orders()) == 1 and len(world.emails()) == 1


def test_reject_after_approve_and_approve_after_reject_are_refused(tmp_path, capsys):
    first = World(tmp_path / "a")
    first.run("decide")
    first.run("approve", "QR-0001")
    assert first.run("reject", "QR-0001") == 1
    assert first.status() == "ordered" and len(first.orders()) == 1
    second = World(tmp_path / "b")
    second.run("decide")
    second.run("reject", "QR-0001")
    assert second.run("approve", "QR-0001") == 1
    assert second.status() == "rejected" and second.orders() == [] and second.emails() == []


def test_issuing_again_on_resumption_writes_no_second_row_or_file(world):
    world.run("decide")
    world.run("approve", "QR-0001")
    before = world.emails()[0].read_bytes()
    conn = db.connect(world.db_path)
    again = quote_decision.issue_order(conn, world.outbox, 1, "SUP-001")
    assert again["number"] == "PO-0001"
    assert len(world.orders()) == 1 and len(world.emails()) == 1
    assert world.emails()[0].read_bytes() == before
    # a different supplier on a re-run cannot create a second order either
    quote_decision.issue_order(conn, world.outbox, 1, "SUP-002")
    assert [o["supplier_code"] for o in world.orders()] == ["SUP-001"]


def test_issue_writes_a_missing_email_when_the_row_already_exists(world):
    world.run("decide")
    conn = db.connect(world.db_path)
    quote_decision.issue_order(conn, world.outbox, 1, "SUP-001")
    (world.outbox / "PO-0001-SUP-001.eml").unlink()  # a crash between the row and the file
    quote_decision.issue_order(conn, world.outbox, 1, "SUP-001")
    assert len(world.orders()) == 1 and len(world.emails()) == 1


def test_a_transient_model_error_is_retried_by_the_extract_node(tmp_path, monkeypatch):
    world = World(tmp_path)
    real = quote_decision.extract_offers
    attempts = []

    import anthropic
    import httpx

    def transient(db_path, request_id, client):
        attempts.append(1)
        if len(attempts) == 1:
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.example.com"))
        return real(db_path, request_id, client)

    monkeypatch.setattr(quote_decision, "extract_offers", transient)
    assert world.run("decide") == 0
    assert len(attempts) == 2 and world.status() == "awaiting_approval"

"""C4 to C10: the quote request graph, its CLI commands and the wait on the simulated clock."""

import datetime
import subprocess
import sys
import time
from email import policy
from email.parser import BytesParser

import pytest

from purchase_cycle import cli, db, llm, quotes, sim_clock
from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.suppliers import SUPPLIER_FAMILIES, SUPPLIERS

CUSTOMER = CUSTOMERS[0].code
# Seeded stock is (i * 37) % 250: nitrile XS has 0 units and three suppliers; latex gloves have two suppliers.
THREE = PRODUCTS[0]
TWO = next(p for p in PRODUCTS if p.family == "Latex examination gloves")
EMAIL = {s.code: s.email for s in SUPPLIERS}


def _suppliers(product):
    return sorted(code for code, family in SUPPLIER_FAMILIES if family == product.family)


def _on_hand(conn, product):
    return conn.execute("SELECT on_hand FROM stock WHERE sku = ?", (product.sku,)).fetchone()[0]


class Env:
    def __init__(self, tmp_path):
        self.db = tmp_path / "b.db"
        self.checkpoints = tmp_path / "checkpoints.sqlite"
        self.outbox = tmp_path / "supplier_outbox"
        self.conn = db.connect(self.db)
        db.seed(self.conn)
        self.saver = sqlite_checkpointer(self.checkpoints)

    def order(self, lines):
        return db.insert_order(self.conn, CUSTOMER, "web_form", "received", lines)

    def short(self, product, units):
        """An order that leaves `product` exactly `units` short."""
        self.order([(product.sku, _on_hand(self.conn, product) + units)])

    def scan(self):
        return quotes.scan(self.db, self.saver, self.outbox)

    def files(self):
        return sorted(p.name for p in self.outbox.glob("*.eml")) if self.outbox.is_dir() else []

    def requests(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM quote_requests ORDER BY id")]

    def request_suppliers(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM quote_request_suppliers ORDER BY 1, 2")]

    def state(self, thread_id):
        graph = quotes.build_quote_request_graph(self.db, self.outbox, checkpointer=self.saver)
        return graph.get_state({"configurable": {"thread_id": thread_id}})


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# Increment 5: scan, send and wait (C4, C5)


def test_scan_starts_one_request_per_short_sku_for_the_exact_shortfall(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm.ModelClient, "extract", lambda *a, **k: calls.append(a))
    env.short(THREE, 8)
    env.short(TWO, 13)
    started = env.scan()
    assert sorted((s["sku"], s["quantity"]) for s in started) == sorted([(THREE.sku, 8), (TWO.sku, 13)])
    rows = env.requests()
    assert [(r["sku"], r["quantity"], r["status"]) for r in rows] == [
        (s["sku"], s["quantity"], "waiting") for s in started
    ]
    first = rows[0]
    assert first["thread_id"] == f"quote-QR-{first['id']:04d}"
    assert (first["created_at"], first["reminder_at"], first["deadline"]) == (
        "2026-10-12 09:00:00",
        "2026-10-15 09:00:00",
        "2026-10-17 09:00:00",
    )
    assert calls == []


def test_a_second_scan_starts_nothing(env):
    env.short(THREE, 8)
    env.scan()
    before = (env.requests(), env.request_suppliers(), env.files())
    assert env.scan() == []
    assert (env.requests(), env.request_suppliers(), env.files()) == before


def test_a_sku_with_enough_stock_starts_nothing(env):
    env.order([(TWO.sku, _on_hand(env.conn, TWO))])
    assert env.scan() == []
    assert env.requests() == [] and env.files() == []


def test_send_writes_one_email_per_supplier_of_the_family(env):
    env.short(TWO, 13)
    env.scan()
    codes = _suppliers(TWO)
    assert len(codes) == 2
    assert env.files() == [f"QR-0001-request-{code}.eml" for code in codes]
    assert [(r["supplier_code"], r["sent_at"]) for r in env.request_suppliers()] == [
        (code, "2026-10-12 09:00:00") for code in codes
    ]
    for code in codes:
        message = BytesParser(policy=policy.default).parsebytes(
            (env.outbox / f"QR-0001-request-{code}.eml").read_bytes()
        )
        assert message["To"].addresses[0].addr_spec == EMAIL[code]
        assert "QR-0001" in message["Subject"]
        body = message.get_content()
        for text in (TWO.sku, TWO.name, TWO.sale_unit, "Quantity: 13", "2026-10-17 09:00"):
            assert text in body


def test_the_email_date_comes_from_the_simulated_clock(env):
    sim_clock.advance(env.conn, hours=5)
    env.short(THREE, 1)
    env.scan()
    message = BytesParser(policy=policy.default).parsebytes((env.outbox / env.files()[0]).read_bytes())
    assert message["Date"].datetime.replace(tzinfo=None) == sim_clock.now(env.conn)


def test_the_thread_pauses_in_wait_with_its_state_in_the_checkpoint(env):
    env.short(THREE, 8)
    env.scan()
    state = env.state("quote-QR-0001")
    assert state.next == ("wait",)
    assert state.values["request_id"] == 1
    assert state.tasks[0].interrupts


def test_re_running_send_writes_no_second_file_or_row(env):
    env.short(THREE, 8)
    env.scan()
    before = (env.request_suppliers(), {p.name: p.read_bytes() for p in env.outbox.iterdir()})
    quotes.send_request(env.db, env.outbox, 1)
    assert (env.request_suppliers(), {p.name: p.read_bytes() for p in env.outbox.iterdir()}) == before


def test_quotes_scan_command_prints_the_started_requests(env, capsys):
    env.short(THREE, 8)
    base = ["--db", str(env.db), "quotes"]
    paths = ["--checkpoints", str(env.checkpoints), "--outbox", str(env.outbox)]
    assert cli.main([*base, "scan", *paths]) == 0
    out = capsys.readouterr().out
    assert "QR-0001" in out and THREE.sku in out and "quantity=8" in out
    assert cli.main([*base, "scan", *paths]) == 0
    assert "no new quote requests" in capsys.readouterr().out


# Increment 6: supplier replies (C6)


def _reply(folder, name, sender, subject):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(
        f"From: {sender}\nTo: purchasing@distributor.example.com\nSubject: {subject}\n\nOur offer is attached.\n",
        encoding="utf-8",
    )
    return path


def _snapshot(env, thread="quote-QR-0001"):
    state = env.state(thread)
    files = {p.name: p.read_bytes() for p in env.outbox.iterdir()}
    return env.requests(), env.request_suppliers(), files, state.config["configurable"]["checkpoint_id"]


def _receive(env, folder):
    return quotes.receive(env.db, env.saver, folder)


@pytest.fixture
def waiting(env):
    """One waiting request QR-0001 for the two-supplier product, created at the clock start."""
    env.short(TWO, 13)
    env.scan()
    return env


def test_an_accepted_reply_marks_the_supplier_as_replied(waiting, tmp_path):
    code = _suppliers(TWO)[0]
    sim_clock.advance(waiting.conn, hours=2)
    path = _reply(tmp_path / "inbox", "r1.eml", EMAIL[code], "Re: Quote request QR-0001 - offer")
    results = _receive(waiting, tmp_path / "inbox")
    assert [(r["reference"], r["supplier"], r["accepted"]) for r in results] == [("QR-0001", code, True)]
    rows = {r["supplier_code"]: r for r in waiting.request_suppliers()}
    assert (rows[code]["replied_at"], rows[code]["reply_file"]) == ("2026-10-12 11:00:00", str(path))
    other = _suppliers(TWO)[1]
    assert (rows[other]["replied_at"], rows[other]["reply_file"]) == (None, None)
    assert waiting.state("quote-QR-0001").next == ("wait",)


@pytest.mark.parametrize(
    ("sender", "subject", "reason"),
    [
        (None, "Re: Quote request QR-0099", "unknown request reference QR-0099"),
        (None, "Re: our offer", "no quote request reference"),
        ("foreign", "Re: Quote request QR-0001", "is not a supplier of QR-0001"),
        ("stranger@nowhere.example.com", "Re: Quote request QR-0001", "is not a supplier of QR-0001"),
    ],
)
def test_a_rejected_reply_changes_nothing(waiting, tmp_path, sender, subject, reason):
    if sender is None:
        sender = EMAIL[_suppliers(TWO)[0]]
    elif sender == "foreign":
        sender = next(email for code, email in EMAIL.items() if code not in _suppliers(TWO))
    before = _snapshot(waiting)
    _reply(tmp_path / "inbox", "r1.eml", sender, subject)
    [result] = _receive(waiting, tmp_path / "inbox")
    assert result["accepted"] is False and reason in result["reason"]
    assert _snapshot(waiting) == before


def test_a_reply_to_a_closed_request_is_rejected(waiting, tmp_path):
    waiting.conn.execute("UPDATE quote_requests SET status = 'no_offers'")
    waiting.conn.commit()
    before = _snapshot(waiting)
    _reply(tmp_path / "inbox", "r1.eml", EMAIL[_suppliers(TWO)[0]], "Re: Quote request QR-0001")
    [result] = _receive(waiting, tmp_path / "inbox")
    assert result["accepted"] is False and "QR-0001 is closed" in result["reason"]
    assert _snapshot(waiting) == before


def test_the_same_reply_twice_changes_nothing_the_second_time(waiting, tmp_path):
    _reply(tmp_path / "inbox", "r1.eml", EMAIL[_suppliers(TWO)[0]], "Re: Quote request QR-0001")
    _receive(waiting, tmp_path / "inbox")
    before = _snapshot(waiting)
    sim_clock.advance(waiting.conn, hours=1)
    [result] = _receive(waiting, tmp_path / "inbox")
    assert result["accepted"] is False and "already replied" in result["reason"]
    assert _snapshot(waiting) == before


def test_quotes_receive_command_prints_each_reply(waiting, tmp_path, capsys):
    _reply(tmp_path / "inbox", "a.eml", EMAIL[_suppliers(TWO)[0]], "Re: Quote request QR-0001")
    _reply(tmp_path / "inbox", "b.eml", EMAIL[_suppliers(TWO)[0]], "Re: Quote request QR-0042")
    args = ["--db", str(waiting.db), "quotes", "receive", str(tmp_path / "inbox")]
    assert cli.main([*args, "--checkpoints", str(waiting.checkpoints)]) == 0
    out = capsys.readouterr().out
    assert "a.eml  accepted  QR-0001" in out
    assert "b.eml  rejected: unknown request reference QR-0042" in out


# Increment 7: ticks and reminders (C7)


def _tick(env):
    return quotes.tick(env.db, env.saver, env.outbox)


def _reminders(env):
    return [name for name in env.files() if "-reminder-" in name]


@pytest.fixture
def three(env, tmp_path):
    """QR-0001 for the three-supplier product, where the first supplier has replied."""
    env.short(THREE, 8)
    env.scan()
    _reply(tmp_path / "inbox", "r1.eml", EMAIL[_suppliers(THREE)[0]], "Re: Quote request QR-0001")
    _receive(env, tmp_path / "inbox")
    return env


def test_a_tick_before_the_reminder_time_writes_nothing(three):
    sim_clock.advance(three.conn, days=2, hours=23)
    files = three.files()
    [result] = _tick(three)
    assert (result["reference"], result["reminders"], result["status"]) == ("QR-0001", 0, "waiting")
    assert three.files() == files


def test_a_tick_at_the_reminder_time_reminds_only_the_suppliers_that_have_not_replied(three):
    sim_clock.advance(three.conn, days=3)
    [result] = _tick(three)
    pending = _suppliers(THREE)[1:]
    assert result["reminders"] == 2
    assert _reminders(three) == [f"QR-0001-reminder-{code}.eml" for code in pending]
    rows = {r["supplier_code"]: r["reminded_at"] for r in three.request_suppliers()}
    assert rows == {_suppliers(THREE)[0]: None, **{code: "2026-10-15 09:00:00" for code in pending}}
    message = BytesParser(policy=policy.default).parsebytes(
        (three.outbox / f"QR-0001-reminder-{pending[0]}.eml").read_bytes()
    )
    assert message["To"].addresses[0].addr_spec == EMAIL[pending[0]]
    assert "QR-0001" in message["Subject"] and "Reminder" in message["Subject"]
    assert message["Date"].datetime.replace(tzinfo=None) == sim_clock.now(three.conn)
    for text in (THREE.sku, THREE.name, "Quantity: 8", "2026-10-17 09:00"):
        assert text in message.get_content()


def test_a_second_tick_before_the_deadline_writes_no_new_reminder(three):
    sim_clock.advance(three.conn, days=3, hours=1)
    _tick(three)
    files = {p.name: p.read_bytes() for p in three.outbox.iterdir()}
    rows = three.request_suppliers()
    assert _tick(three)[0]["reminders"] == 0
    sim_clock.advance(three.conn, days=1)
    assert _tick(three)[0]["reminders"] == 0
    assert {p.name: p.read_bytes() for p in three.outbox.iterdir()} == files
    assert three.request_suppliers() == rows


def test_quotes_tick_command_prints_each_waiting_request(three, capsys):
    sim_clock.advance(three.conn, days=3)
    paths = ["--checkpoints", str(three.checkpoints), "--outbox", str(three.outbox)]
    assert cli.main(["--db", str(three.db), "quotes", "tick", *paths]) == 0
    out = capsys.readouterr().out
    assert "simulated time: 2026-10-15 09:00" in out
    assert "QR-0001  reminders=2  status=waiting" in out


# Increment 8: closing (C8)


def _status(env):
    return env.conn.execute("SELECT status FROM quote_requests WHERE id = 1").fetchone()[0]


def _quote_failures(env):
    return [
        dict(r)
        for r in env.conn.execute(
            "SELECT thread_id, channel, source, step, status FROM failures WHERE step = 'quote_wait' ORDER BY thread_id"
        )
    ]


def _after_close_nothing_changes(env, tmp_path, supplier):
    """A later tick and a later reply write no email and change no row or checkpoint."""
    before = _snapshot(env)
    sim_clock.advance(env.conn, days=2)
    assert _tick(env) == []
    _reply(tmp_path / "late", "late.eml", EMAIL[supplier], "Re: Quote request QR-0001")
    [result] = _receive(env, tmp_path / "late")
    assert result["accepted"] is False and "QR-0001 is closed" in result["reason"]
    assert _snapshot(env) == before


def test_the_request_closes_at_once_when_every_supplier_replied(waiting, tmp_path):
    for i, code in enumerate(_suppliers(TWO)):
        _reply(tmp_path / "inbox", f"r{i}.eml", EMAIL[code], "Re: Quote request QR-0001")
    _receive(waiting, tmp_path / "inbox")
    assert _status(waiting) == "ready_for_decision"
    assert waiting.state("quote-QR-0001").next == ()
    assert _quote_failures(waiting) == []
    _after_close_nothing_changes(waiting, tmp_path, _suppliers(TWO)[0])


def test_the_deadline_tick_closes_as_ready_for_decision_with_one_reply(three, tmp_path):
    sim_clock.advance(three.conn, days=3)
    _tick(three)
    files = three.files()
    sim_clock.advance(three.conn, days=2)
    [result] = _tick(three)
    assert (result["reminders"], result["status"]) == (0, "ready_for_decision")
    assert three.files() == files
    assert _quote_failures(three) == []
    _after_close_nothing_changes(three, tmp_path, _suppliers(THREE)[1])


def test_the_deadline_tick_closes_as_no_offers_with_a_failure_row(waiting, tmp_path):
    sim_clock.advance(waiting.conn, days=3)
    _tick(waiting)
    sim_clock.advance(waiting.conn, days=2)
    [result] = _tick(waiting)
    assert result["status"] == "no_offers"
    assert _quote_failures(waiting) == [
        {
            "thread_id": "quote-QR-0001",
            "channel": "quotes",
            "source": "QR-0001",
            "step": "quote_wait",
            "status": "needs_review",
        }
    ]
    _after_close_nothing_changes(waiting, tmp_path, _suppliers(TWO)[0])


def test_a_single_tick_past_the_deadline_sends_no_reminder(waiting):
    files = waiting.files()
    sim_clock.advance(waiting.conn, days=6)
    [result] = _tick(waiting)
    assert (result["reminders"], result["status"]) == (0, "no_offers")
    assert waiting.files() == files
    assert all(r["reminded_at"] is None for r in waiting.request_suppliers())


# Increment 9: cross-process wait and real-time independence (C9, C3)


def _timeline(env, inbox):
    """Scan, a reply, a reminder tick, a duplicate reply and a deadline tick, as CLI argument lists."""
    common = ["--db", str(env.db)]
    checkpoints = ["--checkpoints", str(env.checkpoints)]
    outbox = ["--outbox", str(env.outbox)]
    return [
        [*common, "quotes", "scan", *checkpoints, *outbox],
        [*common, "clock", "advance", "--days", "1"],
        [*common, "quotes", "receive", str(inbox), *checkpoints],
        [*common, "clock", "advance", "--days", "2"],
        [*common, "quotes", "tick", *checkpoints, *outbox],
        [*common, "quotes", "receive", str(inbox), *checkpoints],
        [*common, "clock", "advance", "--days", "2"],
        [*common, "quotes", "tick", *checkpoints, *outbox],
    ]


def _timeline_env(tmp_path, name):
    """Two short SKUs; the inbox holds one reply to QR-0001 from a supplier of both products."""
    (tmp_path / name).mkdir()
    env = Env(tmp_path / name)
    env.short(THREE, 8)
    env.short(TWO, 13)
    _reply(tmp_path / "inbox", "r1.eml", EMAIL[_suppliers(TWO)[0]], "Re: Quote request QR-0001")
    return env


def _final(env):
    """Rows, outbox bytes and thread states; `failures` times come from SQLite `datetime('now')` and are left out."""
    rows = {
        table: [dict(r) for r in env.conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
        for table in ("quote_requests", "quote_request_suppliers", "sim_clock")
    }
    rows["failures"] = [
        dict(r)
        for r in env.conn.execute("SELECT thread_id, channel, source, step, error, status FROM failures ORDER BY 1")
    ]
    files = {p.name: p.read_bytes() for p in env.outbox.iterdir()}
    saver = sqlite_checkpointer(env.checkpoints)
    graph = quotes.build_quote_request_graph(env.db, env.outbox, checkpointer=saver)
    threads = sorted({c.config["configurable"]["thread_id"] for c in saver.list(None)})
    states = {
        t: (
            graph.get_state({"configurable": {"thread_id": t}}).values,
            graph.get_state({"configurable": {"thread_id": t}}).next,
        )
        for t in threads
    }
    return rows, files, states


def test_a_request_survives_separate_processes(tmp_path):
    single = _timeline_env(tmp_path, "single")
    for argv in _timeline(single, tmp_path / "inbox"):
        assert cli.main(argv) == 0
    separate = _timeline_env(tmp_path, "separate")
    for argv in _timeline(separate, tmp_path / "inbox"):
        run = subprocess.run(
            [sys.executable, "-m", "purchase_cycle.cli", *argv], capture_output=True, text=True, timeout=120
        )
        assert run.returncode == 0, run.stderr
    rows, files, states = _final(separate)
    assert sorted(r["status"] for r in rows["quote_requests"]) == ["no_offers", "ready_for_decision"]
    assert list(states) == ["quote-QR-0001", "quote-QR-0002"]
    assert any("reminder" in name for name in files)
    assert (rows, files, states) == _final(single)


def _freeze_real_time(monkeypatch, moment):
    """Patch the real-time sources this process reads: `time.time` and `datetime.now` in the loaded modules."""
    real = datetime.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is None else moment.replace(tzinfo=datetime.UTC).astimezone(tz)

        @classmethod
        def utcnow(cls):
            return moment

        @classmethod
        def today(cls):
            return moment

    monkeypatch.setattr(datetime, "datetime", Frozen)
    for name, module in list(sys.modules.items()):
        if name.startswith(("purchase_cycle", "langgraph")) and getattr(module, "datetime", None) is real:
            monkeypatch.setattr(module, "datetime", Frozen)
    monkeypatch.setattr(time, "time", lambda: moment.timestamp())


@pytest.mark.parametrize("moments", [(datetime.datetime(2001, 2, 3, 4, 5), datetime.datetime(2039, 11, 30, 23, 59))])
def test_the_same_timeline_gives_the_same_results_whatever_the_real_time(tmp_path, monkeypatch, moments):
    results = []
    for i, moment in enumerate(moments):
        with monkeypatch.context() as patch:
            _freeze_real_time(patch, moment)
            env = _timeline_env(tmp_path, f"run{i}")
            for argv in _timeline(env, tmp_path / "inbox"):
                assert cli.main(argv) == 0
            results.append(_final(env))
    assert results[0] == results[1]


# Increment 10: quotes list (C10)


def test_quotes_list_shows_every_request_with_its_suppliers_and_times(env, tmp_path, capsys):
    other = next(p for p in PRODUCTS if p.family not in (THREE.family, TWO.family))

    def goods_arrive(product):
        # Demand keeps counting after a close, so stock rises and the next scan does not ask for it again.
        with env.conn:
            env.conn.execute("UPDATE stock SET on_hand = on_hand + 100 WHERE sku = ?", (product.sku,))

    env.short(TWO, 13)
    env.scan()
    for i, code in enumerate(_suppliers(TWO)):
        _reply(tmp_path / "all", f"r{i}.eml", EMAIL[code], "Re: Quote request QR-0001")
    _receive(env, tmp_path / "all")
    goods_arrive(TWO)
    env.short(THREE, 8)
    env.scan()
    sim_clock.advance(env.conn, days=6)
    _tick(env)
    goods_arrive(THREE)
    env.short(other, 5)
    env.scan()
    first, *rest = _suppliers(other)
    _reply(tmp_path / "one", "r.eml", EMAIL[first], "Re: Quote request QR-0003")
    _receive(env, tmp_path / "one")

    assert cli.main(["--db", str(env.db), "quotes", "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "quote requests: 3"
    two = ",".join(_suppliers(TWO))
    three = ",".join(_suppliers(THREE))
    assert lines[1:] == [
        f"  QR-0001  sku={TWO.sku}  quantity=13  status=ready_for_decision  replied={two}  pending=-  "
        "next reminder=-  deadline=2026-10-17 09:00",
        f"  QR-0002  sku={THREE.sku}  quantity=8  status=no_offers  replied=-  pending={three}  "
        "next reminder=-  deadline=2026-10-17 09:00",
        f"  QR-0003  sku={other.sku}  quantity=5  status=waiting  replied={first}  pending={','.join(rest)}  "
        "next reminder=2026-10-21 09:00  deadline=2026-10-23 09:00",
    ]

    sim_clock.advance(env.conn, days=3)
    _tick(env)
    assert cli.main(["--db", str(env.db), "quotes", "list"]) == 0
    assert "next reminder=-  deadline=2026-10-23 09:00" in capsys.readouterr().out.splitlines()[3]


def test_quotes_list_with_no_requests(tmp_path, capsys):
    assert cli.main(["--db", str(tmp_path / "b.db"), "quotes", "list"]) == 0
    assert capsys.readouterr().out.strip() == "no quote requests"

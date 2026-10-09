"""C10: a crash at each fault injection point and `purchase-cycle resume` for the three channel graphs."""

import json
import os
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from conftest import make_email
from purchase_cycle import cli, db, faults
from purchase_cycle.email_order import model_text as email_text
from purchase_cycle.email_order import parse_email
from purchase_cycle.llm import EMAIL_EXTRACTION, EMAIL_INTAKE, MATCHING, WHATSAPP_EXTRACTION, WHATSAPP_INTAKE
from purchase_cycle.whatsapp_order import model_text as whatsapp_text
from test_clarification_graph import EMAIL_BODY, EMAIL_LINES, SENDER
from test_web_form import ALCOHOL
from test_whatsapp_order import BODY, CUSTOMER, LINES, ORDER, message

STEP = textwrap.dedent(
    """
    import dataclasses, socket, sys
    from pathlib import Path
    from purchase_cycle import cli

    def blocked(*args, **kwargs):
        raise OSError("network blocked by test")

    socket.socket.connect = blocked
    socket.create_connection = blocked
    recordings = Path(sys.argv[1])
    for name in ("MATCHING", "EMAIL_INTAKE", "EMAIL_EXTRACTION", "WHATSAPP_INTAKE", "WHATSAPP_EXTRACTION",
                 "CLARIFICATION_QUESTION", "CLARIFICATION_ANSWER"):
        setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=recordings))
    sys.exit(cli.main(sys.argv[2:]))
    """
)
FORM = {
    "submission_id": "WF-CRASH-1",
    "customer_code": CUSTOMER.code,
    "lines": [{"product": "GLV-NIT-M", "quantity": 40}, {"product": ALCOHOL, "quantity": 12}],
}
POINTS = (faults.AFTER_CHANNEL_STEPS, faults.AFTER_STORE_COMMIT, faults.IN_REPLY)


def _web_form(inbox, catalog, write):
    (inbox / "WF-CRASH-1.json").write_text(json.dumps(FORM), encoding="utf-8")
    return write(catalog, ALCOHOL, {"sku": "ALC70-250"}, task=MATCHING), [("GLV-NIT-M", 40), ("ALC70-250", 12)]


def _email(inbox, catalog, write):
    path = make_email(inbox / "MSG-1.eml", SENDER, "Order", EMAIL_BODY.replace(", plus 2 hospital beds", ""))
    text = email_text(parse_email(path.read_bytes()))
    write(catalog, text, {"is_order": True, "reason": "An order."}, task=EMAIL_INTAKE)
    lines = EMAIL_LINES[:2]
    return write(catalog, text, {"lines": lines}, task=EMAIL_EXTRACTION), [(x["sku"], x["quantity"]) for x in lines]


def _whatsapp(inbox, catalog, write):
    (inbox / "WA-1.json").write_text(json.dumps(message("wamid.CRASH")), encoding="utf-8")
    text = whatsapp_text({"body": BODY})
    write(catalog, text, ORDER, task=WHATSAPP_INTAKE)
    return write(catalog, text, {"lines": LINES[:1]}, task=WHATSAPP_EXTRACTION), [("GLV-NIT-M", 40)]


CHANNELS = {"web_form": _web_form, "email": _email, "whatsapp": _whatsapp}


def _step(recordings, db_path, *argv, crash_at=None):
    env = {k: v for k, v in os.environ.items() if k != faults.CRASH_AT}
    if crash_at:
        env[faults.CRASH_AT] = crash_at
    command = [sys.executable, "-c", STEP, str(recordings), "--db", str(db_path), *argv]
    return subprocess.run(command, capture_output=True, text=True, env=env)


def _rows(db_path, sql):
    conn = db.connect(db_path)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("channel", list(CHANNELS))
def test_a_crash_at_each_point_is_resumed_in_a_new_process_with_exactly_one_order(
    seeded_db, write_recording, tmp_path, channel, point
):
    db_path, catalog = seeded_db
    inbox, outbox, checkpoints = tmp_path / "inbox", tmp_path / "outbox", str(tmp_path / "c.db")
    inbox.mkdir()
    recordings, expected = CHANNELS[channel](inbox, catalog, write_recording)
    options = ["--checkpoints", checkpoints, "--outbox", str(outbox)]

    crashed = _step(recordings, db_path, "route", str(inbox), *options, crash_at=point)
    assert crashed.returncode == faults.EXIT_CODE, crashed.stderr
    stored_before = _rows(db_path, "SELECT COUNT(*) FROM orders")[0][0]
    assert stored_before == (0 if point == faults.AFTER_CHANNEL_STEPS else 1)
    with sqlite3.connect(checkpoints) as conn:
        [(thread_id,)] = conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
    assert thread_id.startswith(f"{channel}-")

    resumed = _step(recordings, db_path, "resume", thread_id, *options)
    assert resumed.returncode == 0, resumed.stderr
    assert f"thread_id={thread_id}  channel={channel}" in resumed.stdout
    assert "stored order: 1" in resumed.stdout
    assert _rows(db_path, "SELECT id, channel FROM orders") == [(1, channel)]
    assert _rows(db_path, "SELECT sku, quantity FROM order_lines WHERE order_id = 1 ORDER BY id") == expected
    assert _rows(db_path, "SELECT channel, thread_id, order_id FROM order_sources") == [(channel, thread_id, 1)]
    if channel == "whatsapp":
        [reply] = outbox.iterdir()
        assert reply.name == "reply-wamid.CRASH.json"
        assert "registered as order 1" in json.loads(reply.read_text(encoding="utf-8"))["text"]
    else:
        assert "registered as order 1" in resumed.stdout
    assert "network blocked" not in resumed.stderr + crashed.stderr


def test_resume_of_a_thread_with_no_checkpoint_fails_with_a_message(seeded_db, tmp_path, capsys):
    db_path, _ = seeded_db
    argv = ["--db", str(db_path), "resume", "email-run-MSG-9", "--checkpoints", str(tmp_path / "c.db")]
    assert cli.main(argv) == 1
    assert "no checkpoint to resume found for thread email-run-MSG-9" in capsys.readouterr().err


def test_resume_of_a_thread_without_a_channel_prefix_fails_with_a_message(seeded_db, tmp_path, capsys):
    db_path, _ = seeded_db
    assert cli.main(["--db", str(db_path), "resume", "run-1", "--checkpoints", str(tmp_path / "c.db")]) == 1
    assert "thread run-1 has no channel prefix" in capsys.readouterr().err

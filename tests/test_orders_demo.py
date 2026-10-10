"""C12: `orders-demo` runs the sample mixed inbox in replay with the network blocked in every process."""

import os
import sqlite3
import subprocess
import sys
import textwrap

from purchase_cycle import faults

# Loaded by every Python process the test starts, the demo and its crash and resume children: it notes the process
# and blocks the network, noting each attempt.
SITECUSTOMIZE = textwrap.dedent(
    """
    import os, socket

    LOG = os.environ["ORDERS_DEMO_TEST_LOG"]

    def note(text):
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(text + "\\n")

    def blocked(*args, **kwargs):
        note("network")
        raise OSError("network blocked by test")

    note("process")
    socket.socket.connect = blocked
    socket.create_connection = blocked
    """
)


def _item(out: str, name: str) -> list[str]:
    """The printed block of one inbox item, from its header to the next item or the summary."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"== {name} "))
    ends = (i for i in range(start + 1, len(lines)) if lines[i].startswith(("== ", "summary:")))
    return [line for line in lines[start : next(ends, len(lines))] if line.strip()]


def _reply(block: list[str]) -> str:
    return "\n".join(block[block.index("reply:") + 1 :])


def test_orders_demo_runs_the_sample_inbox_in_replay_with_the_network_blocked(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    log = tmp_path / "processes.log"
    env = {k: v for k, v in os.environ.items() if k != faults.CRASH_AT}
    env.update(PYTHONPATH=str(site), ORDERS_DEMO_TEST_LOG=str(log))
    workdir = tmp_path / "work"
    command = [sys.executable, "-m", "purchase_cycle.cli", "orders-demo", "--workdir", str(workdir)]
    run = subprocess.run(command, capture_output=True, text=True, env=env)
    out = run.stdout
    assert run.returncode == 0, out + run.stderr
    assert run.stderr == ""
    noted = log.read_text(encoding="utf-8").split()
    assert noted.count("process") >= 3  # the demo, the crashed process and the resume process
    assert "network" not in noted

    assert "mode=replay" in out
    expected = {
        "01-web-form.json": ("web_form", "web_form", "stored order 1", "1"),
        "02-email-order.eml": ("email", "email", "stored order 2", "2"),
        "03-whatsapp-order.json": ("whatsapp", "whatsapp_new", "stored order 3", "3"),
        "04-whatsapp-doubt.json": ("whatsapp", "whatsapp_new", "question asked, paused, nothing stored", "none"),
        "05-whatsapp-photo.json": (
            "whatsapp",
            "whatsapp_new",
            "rejected, nothing stored: the message type 'image' is not text; only text is read",
            "none",
        ),
        "06-whatsapp-order-again.json": (
            "whatsapp",
            "duplicate",
            "re-delivery of a message already stored as order 3, nothing run or stored",
            "3",
        ),
        "07-whatsapp-answer.json": ("whatsapp", "whatsapp_answer", "stored order 4", "4"),
        "08-web-form-crash.json": ("web_form", "web_form", "stored order 5", "5"),
    }
    for name, (channel, route, outcome, order) in expected.items():
        block = _item(out, name)
        assert block[0] == f"== {name}  channel={channel}  route={route}", block
        assert f"outcome: {outcome}" in block, block
        assert f"order: {order}" in block, block
        reply = _reply(block)
        assert reply.strip(), block
        if order != "none":
            assert f"order {order}" in reply, block

    assert "scene: retry" in _item(out, "01-web-form.json")[1]
    assert "scene: re-ask" in _item(out, "02-email-order.eml")[1]
    crash = _item(out, "08-web-form-crash.json")
    assert "scene: crash" in crash[1]
    assert f"first process exit code: {faults.EXIT_CODE}" in crash
    assert "second process (purchase-cycle resume) exit code: 0" in crash
    assert "nitrile gloves" in _reply(_item(out, "04-whatsapp-doubt.json")).lower()
    assert "text" in _reply(_item(out, "05-whatsapp-photo.json")).lower()

    summary = out[out.index("summary:") :].splitlines()
    assert "stored orders: 5" in summary
    assert "parked failures: none" in summary
    conn = sqlite3.connect(workdir / "business.db")
    try:
        assert conn.execute("SELECT count(*) FROM orders").fetchone() == (5,)
        assert conn.execute("SELECT count(*) FROM failures").fetchone() == (0,)
    finally:
        conn.close()
    outbox = sorted(path.name for path in (workdir / "outbox").iterdir())
    # The question has its own file; the answer reply goes to the file of the paused order's message, and the
    # re-delivery overwrites the reply of the order it repeats.
    assert outbox == [
        "question-34600103203-wamid.ORDERS-04-1.json",
        "reply-34600102202-wamid.ORDERS-03.json",
        "reply-34600103203-wamid.ORDERS-04.json",
        "reply-34600104204-wamid.ORDERS-05.json",
    ]


def test_orders_demo_refuses_a_workdir_whose_outbox_is_the_real_outbox(tmp_path, monkeypatch, capsys):
    from purchase_cycle import cli, config

    data = tmp_path / "data"
    outbox = data / "outbox"
    outbox.mkdir(parents=True)
    reply = outbox / "reply-34600000000-WA-1.json"
    reply.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "OUTBOX_DIR", outbox)
    for workdir in (data, tmp_path / "data" / ".", tmp_path / "other" / ".." / "data"):
        assert cli.main(["orders-demo", "--workdir", str(workdir)]) == 1
        assert "Error:" in capsys.readouterr().err
        assert reply.exists()
    assert sorted(p.name for p in data.iterdir()) == ["outbox"]

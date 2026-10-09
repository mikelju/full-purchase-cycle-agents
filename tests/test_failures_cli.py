"""C9 (phase 05): `purchase-cycle failures list` and `failures resume <thread_id>` over parked threads."""

import dataclasses
import subprocess
import sys
import textwrap

import pytest

from conftest import make_email
from purchase_cycle import cli, db
from purchase_cycle.email_order import model_text, parse_email
from purchase_cycle.llm import CORRECTION, EMAIL_EXTRACTION, EMAIL_INTAKE
from test_clarification_graph import EMAIL_BODY, EMAIL_LINES, SENDER

LINES = EMAIL_LINES[:2]
BAD = {"lines": [{**LINES[0], "sku": "GLV-NIT-XXL"}, LINES[1]]}
ERROR = "Extracted line 1: SKU 'GLV-NIT-XXL' is not in the catalog"

RESUME_STEP = textwrap.dedent(
    """
    import dataclasses, socket, sys
    from pathlib import Path
    from purchase_cycle import cli

    def blocked(*args, **kwargs):
        raise OSError("network blocked by test")

    socket.socket.connect = blocked
    socket.create_connection = blocked
    recordings = Path(sys.argv[1])
    for name in ("EMAIL_INTAKE", "EMAIL_EXTRACTION"):
        setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=recordings))
    sys.exit(cli.main(sys.argv[2:]))
    """
)


class Parked:
    """An email order parked by `route` after two invalid extractions; `fix()` adds a valid recording."""

    def __init__(self, seeded_db, write_recording, tmp_path, monkeypatch, capsys):
        self.db_path, self.catalog = seeded_db
        self.tmp_path, self.write = tmp_path, write_recording
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        self.path = make_email(inbox / "MSG-1.eml", SENDER, "Order", EMAIL_BODY.replace(", plus 2 hospital beds", ""))
        self.text = model_text(parse_email(self.path.read_bytes()))
        write_recording(self.catalog, self.text, {"is_order": True, "reason": "An order."}, task=EMAIL_INTAKE)
        write_recording(self.catalog, self.text, BAD, task=EMAIL_EXTRACTION)
        self.recordings = write_recording(
            self.catalog, self.text + CORRECTION.format(error=ERROR), BAD, task=EMAIL_EXTRACTION
        )
        for name in ("EMAIL_INTAKE", "EMAIL_EXTRACTION"):
            monkeypatch.setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=self.recordings))
        assert self.run("route", str(inbox), "--outbox", str(tmp_path / "outbox")) == 1
        capsys.readouterr()
        [row] = self.failures()
        self.thread_id = row["thread_id"]

    def run(self, *args) -> int:
        command, rest = args[0], args[1:]
        options = [] if args[:2] == ("failures", "list") else ["--checkpoints", str(self.tmp_path / "c.db")]
        return cli.main(["--db", str(self.db_path), command, *rest, *options])

    def fix(self):
        """The reviewer adds a valid answer for the extraction; in replay the latest recording of a key wins."""
        self.write(self.catalog, self.text, {"lines": LINES}, task=EMAIL_EXTRACTION)

    def failures(self) -> list[dict]:
        conn = db.connect(self.db_path)
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM failures ORDER BY thread_id")]
        finally:
            conn.close()

    def order_lines(self) -> list[tuple]:
        conn = db.connect(self.db_path)
        try:
            return [tuple(r) for r in conn.execute("SELECT sku, quantity FROM order_lines ORDER BY id")]
        finally:
            conn.close()


@pytest.fixture
def parked(seeded_db, write_recording, tmp_path, monkeypatch, capsys, no_network):
    return Parked(seeded_db, write_recording, tmp_path, monkeypatch, capsys)


def test_failures_list_with_no_parked_thread(seeded_db, capsys):
    assert cli.main(["--db", str(seeded_db[0]), "failures", "list"]) == 0
    assert capsys.readouterr().out == "no parked threads\n"


def test_failures_list_shows_each_parked_thread(parked, capsys):
    assert parked.run("failures", "list") == 0
    out = capsys.readouterr().out
    assert out.startswith("parked threads: 1\n")
    assert (
        f"  {parked.thread_id}  channel=email  source={parked.path}  step=extract  age=0h00m\n"
        f"    error: Second invalid model answer: {ERROR}\n"
    ) in out


def test_failures_resume_in_a_new_process_completes_the_order_and_resolves_the_row(parked):
    parked.fix()
    step = subprocess.run(
        [
            sys.executable,
            "-c",
            RESUME_STEP,
            str(parked.recordings),
            "--db",
            str(parked.db_path),
            "failures",
            "resume",
            parked.thread_id,
            "--checkpoints",
            str(parked.tmp_path / "c.db"),
        ],
        capture_output=True,
        text=True,
    )
    assert step.returncode == 0, step.stderr
    assert f"thread_id={parked.thread_id}  channel=email  step=extract" in step.stdout
    assert "stored order: 1" in step.stdout
    assert "failure resolved" in step.stdout
    assert parked.order_lines() == [(line["sku"], line["quantity"]) for line in LINES]
    [row] = parked.failures()
    assert row["status"] == "resolved"


def test_failures_resume_that_fails_again_keeps_the_thread_parked(parked, capsys):
    assert parked.run("failures", "resume", parked.thread_id) == 1
    err = capsys.readouterr().err
    assert f"Error: thread {parked.thread_id} failed again: NeedsReview: Second invalid model answer" in err
    [row] = parked.failures()
    assert row["status"] == "needs_review"
    assert parked.order_lines() == []


def test_failures_resume_of_a_thread_that_is_not_parked_changes_nothing(parked, capsys):
    before = parked.failures()
    assert parked.run("failures", "resume", "email-unknown") == 1
    assert capsys.readouterr().err == "Error: thread email-unknown is not parked; nothing changed\n"
    assert parked.failures() == before
    parked.fix()
    assert parked.run("failures", "resume", parked.thread_id) == 0
    capsys.readouterr()
    resolved = parked.failures()
    assert parked.run("failures", "resume", parked.thread_id) == 1
    assert capsys.readouterr().err == (
        f"Error: thread {parked.thread_id} is not parked (status resolved); nothing changed\n"
    )
    assert parked.failures() == resolved
    assert parked.order_lines() == [(line["sku"], line["quantity"]) for line in LINES]


@pytest.mark.parametrize("command", ["failures", "resume"])
def test_resume_commands_save_the_channel_recordings(parked, monkeypatch, capsys, command):
    """In record mode the intake and extraction recordings are saved, not only the clarification ones."""
    from purchase_cycle.llm import ModelClient

    parked.fix()
    if command == "resume":
        conn = db.connect(parked.db_path)
        try:
            db.resolve_failure(conn, parked.thread_id)
        finally:
            conn.close()
    saved = []
    monkeypatch.setattr(ModelClient, "save_recordings", lambda self: saved.append(self.task.name) or 0)
    args = ("failures", "resume") if command == "failures" else ("resume",)
    assert parked.run(*args, parked.thread_id) == 0, capsys.readouterr()
    assert {EMAIL_INTAKE.name, EMAIL_EXTRACTION.name} <= set(saved)

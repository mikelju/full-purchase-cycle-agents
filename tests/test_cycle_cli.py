"""Phase 12 increment 6: the `cycle run` command and the restart across processes (C2, C4, C6)."""

import json
import os
import subprocess
import sys

from purchase_cycle import cycle
from purchase_cycle.llm import CLAIM_DRAFTING, build_system_prompt, recording_key
from test_cycle import (
    CountingClient,
    Journey,
    _snapshot,
)

CLAIM_ANSWERS = {CLAIM_DRAFTING.name: {"": {"body": "Dear supplier, please review the invoice."}}}


class RecordingClient(CountingClient):
    """The counting double that also keeps each answer under the key a replay ModelClient looks it up by."""

    def __init__(self, task, calls, answers, catalog, recorded):
        super().__init__(task, calls, answers)
        self.prompt, self.recorded = build_system_prompt(catalog, task), recorded

    def extract(self, sentence, case_id=None, correction=None):
        answer = super().extract(sentence, case_id, correction)
        key = recording_key(self.prompt, sentence, self.task)
        self.recorded[key] = {
            "key": key,
            "case_id": None,
            "sentence": sentence,
            "answer": answer.model_dump(mode="json"),
        }
        return answer


class InProcess(Journey):
    """The journey in one process, recording every model answer for the replay of the other world."""

    def __init__(self, root, answers=None):
        super().__init__(root, answers)
        self.recorded = {}

    def client(self, task, catalog):
        return RecordingClient(task, self.calls, self.answers, catalog, self.recorded)


class Processes(Journey):
    """The same journey with each pass and each command as a new `python -m purchase_cycle.cli` process."""

    def __init__(self, root, recordings, answers=None):
        super().__init__(root, answers)
        self.recordings, self.outputs, self.shelf = recordings, [], recordings.with_suffix(".off")

    def _run(self, *words):
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LANGSMITH_API_KEY")}
        command = [sys.executable, "-m", "purchase_cycle.cli", "--db", str(self.root / "business.db"), *words]
        run = subprocess.run(command, capture_output=True, text=True, env=env)
        assert run.returncode == 0, run.stderr
        self.outputs.append(run.stdout)
        return run

    def one_pass(self, with_recordings=True):
        f = self.folders
        words = ["cycle", "run", "--checkpoints", self.cp, "--mode", "replay", "--recordings", str(self.recordings)]
        for flag, path in (
            ("--orders", f.orders),
            ("--replies", f.replies),
            ("--documents", f.documents),
            ("--signed-notes", f.signed_notes),
            ("--outbox", f.outbox),
            ("--supplier-outbox", f.supplier_outbox),
            ("--delivery-notes", f.delivery_notes),
            ("--invoices", f.invoices),
        ):
            words += [flag, str(path)]
        if not with_recordings:
            self.recordings.replace(self.shelf)
        try:
            return self._run(*words).stdout
        finally:
            if not with_recordings:
                self.shelf.replace(self.recordings)

    def cli(self, *words):
        """A decision or a clock step with the recordings file out of the way: a model call would fail."""
        self.recordings.replace(self.shelf)
        try:
            self._run(*words)
        finally:
            self.shelf.replace(self.recordings)
        return 0


def _journey(world):
    """Order short of stock -> offer approved -> invoice price differs -> claim approved -> sales."""
    world.to_approval()
    world.approve_offer()
    world.drop_documents(5.0)
    world.one_pass()
    world.cli("reconcile", "approve", "INV-0001", "--checkpoints", world.cp, "--outbox", str(world.root / "so2"))
    world.one_pass()


def test_a_pass_prints_what_each_stage_did_and_each_pending_decision_with_its_command_and_a_second_run_nothing_new(
    tmp_path,
):
    (tmp_path / "mem").mkdir()
    mem = InProcess(tmp_path / "mem", CLAIM_ANSWERS)
    mem.to_approval()
    rec = tmp_path / "recordings.jsonl"
    rec.write_text("".join(json.dumps(r) + "\n" for r in mem.recorded.values()), encoding="utf-8")

    (tmp_path / "proc").mkdir()
    proc = Processes(tmp_path / "proc", rec, CLAIM_ANSWERS)
    proc.order_form()
    first = proc.one_pass()
    assert "mode=replay  pass=cycle-run-0001 (new)" in first
    assert "orders: 1 items, 1 stored" in first
    assert "quotes: 1 started" in first
    assert "pending decisions: 0" in first
    (proc.root / "replies" / "QR-0001-SUP-002.eml").write_bytes(
        (mem.root / "replies" / "QR-0001-SUP-002.eml").read_bytes()
    )
    proc.cli("clock", "advance", "--days", "6")
    second = proc.one_pass()
    assert "quotes: 1 replies accepted" in second
    assert "pending decisions: 1" in second
    assert "  quote_approval QR-0001" in second
    assert "    purchase-cycle quotes approve QR-0001" in second
    assert "    purchase-cycle quotes reject QR-0001" in second
    third = proc.one_pass(with_recordings=False)  # a second run with nothing new makes no model call
    for stage in cycle.STAGES:
        assert f"  {stage}: nothing new" in third
    assert "pending decisions: 1" in third


def test_each_pass_and_each_decision_in_a_new_process_gives_the_same_rows_and_files_as_one_process(tmp_path):
    (tmp_path / "mem").mkdir()
    (tmp_path / "proc").mkdir()
    mem = InProcess(tmp_path / "mem", CLAIM_ANSWERS)
    _journey(mem)
    rec = tmp_path / "recordings.jsonl"
    rec.write_text("".join(json.dumps(r) + "\n" for r in mem.recorded.values()), encoding="utf-8")

    proc = Processes(tmp_path / "proc", rec, CLAIM_ANSWERS)
    _journey(proc)
    last = proc.one_pass(with_recordings=False)  # a pass with nothing new calls no model

    mem_rows, mem_files = _snapshot(mem.root)
    proc_rows, proc_files = _snapshot(proc.root)
    assert mem_rows["claims"] and mem_rows["customer_invoices"] and mem_files
    assert proc_rows == mem_rows
    assert proc_files == mem_files
    assert "pending decisions: 0" in last

import json
import socket

import pytest

from purchase_cycle import db
from purchase_cycle.llm import build_system_prompt, recording_key

SENTENCE = "Please send 40 boxes of powder-free nitrile gloves, size M"


@pytest.fixture
def seeded_db(tmp_path):
    path = tmp_path / "business.db"
    conn = db.connect(path)
    db.seed(conn)
    catalog = db.catalog_rows(conn)
    conn.close()
    return path, catalog


@pytest.fixture
def write_recording(tmp_path):
    """Store one recorded answer for a sentence and return the recordings path."""

    def _write(catalog, sentence, answer, case_id="TEST-1"):
        path = tmp_path / "recordings.jsonl"
        key = recording_key(build_system_prompt(catalog), sentence)
        row = {"key": key, "case_id": case_id, "sentence": sentence, "answer": answer}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return path

    return _write


@pytest.fixture
def no_network(monkeypatch):
    """Fail on any outgoing connection and record the attempts."""
    attempts = []

    def guard(*args, **kwargs):
        attempts.append(args)
        raise OSError("network blocked by test")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    return attempts

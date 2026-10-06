"""C3, C6 and C7: graph result, checkpoint resumption across processes and tracing off."""

import json
import os
import subprocess
import sys
import textwrap
import warnings

import pytest

from conftest import SENTENCE
from purchase_cycle import cli, config
from purchase_cycle.graph import build_graph
from purchase_cycle.llm import ModelClient

RUN_STEP = textwrap.dedent(
    """
    import json, sys
    from purchase_cycle import db
    from purchase_cycle.graph import build_graph, sqlite_checkpointer
    from purchase_cycle.llm import ModelClient

    db_path, recordings, checkpoints, phase = sys.argv[1:5]
    conn = db.connect(db_path)
    client = ModelClient("replay", db.catalog_rows(conn), recordings)
    conn.close()
    run = {"configurable": {"thread_id": "resume-test"}}
    if phase == "first":
        graph = build_graph(client, db_path, sqlite_checkpointer(checkpoints), interrupt_after=["extract"])
        state = graph.invoke({"sentence": sys.argv[5]}, run)
    else:
        graph = build_graph(client, db_path, sqlite_checkpointer(checkpoints))
        state = graph.invoke(None, run)
    print(json.dumps({"calls": client.calls, "next": list(graph.get_state(run).next), "state": state}))
    """
)


def _step(*args):
    out = subprocess.run([sys.executable, "-c", RUN_STEP, *map(str, args)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_graph_extracts_and_matches(seeded_db, write_recording):
    db_path, catalog = seeded_db
    path = write_recording(catalog, SENTENCE, {"sku": "GLV-NIT-M", "quantity": 40})
    state = build_graph(ModelClient("replay", catalog, path), db_path).invoke({"sentence": SENTENCE})
    assert state["extracted"] == {"sku": "GLV-NIT-M", "quantity": 40}
    assert state["matched"] is True
    assert state["product"]["name"] == "Nitrile examination gloves, powder-free, size M"


def test_unknown_sku_is_not_matched(seeded_db, write_recording):
    db_path, catalog = seeded_db
    path = write_recording(catalog, SENTENCE, {"sku": "NOT-A-SKU", "quantity": 40})
    state = build_graph(ModelClient("replay", catalog, path), db_path).invoke({"sentence": SENTENCE})
    assert (state["matched"], state["product"]) == (False, None)


def test_resume_in_new_process_skips_the_model(seeded_db, write_recording, tmp_path):
    db_path, catalog = seeded_db
    recordings = write_recording(catalog, SENTENCE, {"sku": "GLV-NIT-M", "quantity": 40})
    checkpoints = tmp_path / "checkpoints.db"

    first = _step(db_path, recordings, checkpoints, "first", SENTENCE)
    assert first["calls"] == 1
    assert first["next"] == ["match"]
    assert "product" not in first["state"]

    second = _step(db_path, recordings, checkpoints, "second")
    assert second["calls"] == 0
    assert second["next"] == []
    assert second["state"]["product"]["sku"] == "GLV-NIT-M"


def test_no_tracing_without_langsmith_variables(seeded_db, write_recording, monkeypatch, no_network, capsys):
    for name in list(os.environ):
        if name.startswith(("LANGSMITH_", "LANGCHAIN_")):
            monkeypatch.delenv(name)
    db_path, catalog = seeded_db
    path = write_recording(catalog, SENTENCE, {"sku": "GLV-NIT-M", "quantity": 40})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build_graph(ModelClient("replay", catalog, path), db_path).invoke({"sentence": SENTENCE})
    from langsmith.utils import tracing_is_enabled

    assert tracing_is_enabled() is False
    assert no_network == []
    assert caught == []
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_replay_mode_forces_tracing_off(monkeypatch):
    from langsmith.utils import get_env_var, tracing_is_enabled

    for name in ("LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
        monkeypatch.setenv(name, "true")
    get_env_var.cache_clear()
    try:
        # LangSmith has already read and cached the variables before replay turns them off.
        assert tracing_is_enabled() is True
        config.configure_tracing("replay")
        assert tracing_is_enabled() is False
    finally:
        get_env_var.cache_clear()


def test_resume_needs_a_known_thread(tmp_path, monkeypatch, capsys, request):
    from langsmith.utils import get_env_var

    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    # The CLI turns tracing off for replay; restore the variables and the LangSmith cache afterwards.
    for name in ("LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
        monkeypatch.delenv(name, raising=False)
    request.addfinalizer(get_env_var.cache_clear)
    base = ["--db", str(tmp_path / "business.db"), "demo", "--resume", "--checkpoints", str(tmp_path / "cp.db")]
    with pytest.raises(SystemExit) as stopped:
        cli.main(base)
    assert stopped.value.code == 2
    assert "--resume needs --thread-id" in capsys.readouterr().err
    assert cli.main([*base, "--thread-id", "unknown"]) == 1
    assert "no checkpoint found for thread unknown" in capsys.readouterr().err

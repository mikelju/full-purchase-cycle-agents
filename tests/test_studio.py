"""Change 004, C1 to C3: the LangGraph Studio entry points run the channel graphs on a Studio database and outbox."""

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from purchase_cycle import config, db, studio

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "examples" / "orders"
DOUBT_ANSWER = "Size L please, all of them."  # examples/orders/07-whatsapp-answer.json, recorded in orders_demo.jsonl


@pytest.fixture
def studio_paths(tmp_path, monkeypatch):
    """Point the Studio database and outbox at a temporary folder, with no key and tracing asked for."""
    monkeypatch.setattr(studio, "DB_PATH", tmp_path / "studio" / "purchase_cycle.db")
    monkeypatch.setattr(studio, "OUTBOX_DIR", tmp_path / "studio" / "outbox")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PURCHASE_CYCLE_STUDIO_MODE", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    return tmp_path / "studio"


def _stored_order(db_path: Path, order_id: int) -> int:
    conn = db.connect(db_path)
    try:
        return conn.execute("SELECT count(*) FROM order_lines WHERE order_id = ?", (order_id,)).fetchone()[0]
    finally:
        conn.close()


def test_langgraph_json_exposes_the_three_factories():
    spec = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    assert spec["dependencies"] == ["."]
    assert spec["graphs"] == {
        "web_form": "./src/purchase_cycle/studio.py:web_form_graph",
        "email": "./src/purchase_cycle/studio.py:email_graph",
        "whatsapp": "./src/purchase_cycle/studio.py:whatsapp_graph",
    }
    assert "env" not in spec  # loading .env could turn tracing on
    assert ".langgraph_api/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_studio_paths_are_apart_from_the_business_database():
    assert studio.DB_PATH == config.DATA_DIR / "studio" / "purchase_cycle.db"
    assert studio.OUTBOX_DIR == config.DATA_DIR / "studio" / "outbox"
    assert studio.DB_PATH != config.default_db_path()
    assert studio.OUTBOX_DIR != config.OUTBOX_DIR


def test_factories_compile_without_checkpointer_on_the_seeded_studio_database(studio_paths):
    for factory in (studio.web_form_graph, studio.email_graph, studio.whatsapp_graph):
        assert factory().checkpointer is None
    conn = db.connect(studio.DB_PATH)
    try:
        assert db.catalog_rows(conn)
    finally:
        conn.close()


def test_tracing_is_forced_off_and_replay_is_the_default(studio_paths, monkeypatch):
    import os

    studio.web_form_graph()
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert studio.studio_mode() == "replay"
    monkeypatch.setenv("PURCHASE_CYCLE_STUDIO_MODE", "live")
    assert studio.studio_mode() == "live"
    monkeypatch.setenv("PURCHASE_CYCLE_STUDIO_MODE", "record")
    with pytest.raises(ValueError, match="PURCHASE_CYCLE_STUDIO_MODE"):
        studio.web_form_graph()


def test_live_mode_loads_the_key_from_dotenv_and_keeps_tracing_off(studio_paths, monkeypatch):
    import os

    def fake_load_dotenv():
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        os.environ["LANGSMITH_TRACING"] = "true"  # the owner's .env may ask for traces

    monkeypatch.setattr(studio, "load_dotenv", fake_load_dotenv)
    monkeypatch.setenv("PURCHASE_CYCLE_STUDIO_MODE", "live")
    studio.web_form_graph()
    assert os.environ["ANTHROPIC_API_KEY"] == "test-key"
    assert os.environ["LANGSMITH_TRACING"] == "false"


@pytest.mark.parametrize(
    "channel, inputs",
    [
        ("web_form", {"submission": json.loads((SAMPLES / "01-web-form.json").read_text(encoding="utf-8"))}),
        ("email", {"email_path": str(SAMPLES / "02-email-order.eml")}),
        ("whatsapp", {"message_path": str(SAMPLES / "03-whatsapp-order.json")}),
    ],
)
def test_replay_stores_the_clear_sample_order(studio_paths, no_network, channel, inputs):
    graph = studio.build(channel, MemorySaver())
    state = graph.invoke(inputs, {"configurable": {"thread_id": f"studio-{channel}"}})
    assert "__interrupt__" not in state
    assert state["errors"] == []
    assert state["order_id"] is not None
    assert _stored_order(studio.DB_PATH, state["order_id"]) > 0
    if channel == "whatsapp":
        assert Path(state["outbox_file"]).parent == studio.OUTBOX_DIR
    assert no_network == []


@pytest.mark.parametrize(
    "resume",
    [
        {"answer": DOUBT_ANSWER},
        json.dumps({"answer": DOUBT_ANSWER}),  # Studio's resume box sends the typed JSON as text
        f"  {DOUBT_ANSWER}\n",  # or the plain answer
        json.dumps(DOUBT_ANSWER),  # or the plain answer typed inside the box's "" prefill
    ],
    ids=["dict", "json-text", "plain-text", "json-string"],
)
def test_replay_whatsapp_doubt_pauses_and_resumes_with_the_recorded_answer(studio_paths, no_network, resume):
    graph = studio.build("whatsapp", MemorySaver())
    run = {"configurable": {"thread_id": "studio-doubt"}}
    paused = graph.invoke({"message_path": str(SAMPLES / "04-whatsapp-doubt.json")}, run)
    (pending,) = paused["__interrupt__"]
    assert set(pending.value) == {"question", "round", "doubts", "rejected"}
    assert pending.value["question"]
    assert paused.get("order_id") is None
    state = graph.invoke(Command(resume=resume), run)
    assert "__interrupt__" not in state
    assert state["order_id"] is not None
    assert _stored_order(studio.DB_PATH, state["order_id"]) > 0
    assert no_network == []


@pytest.mark.parametrize(
    "resume",
    ['""', "", 42, [], {"message_id": "wamid-1"}],  # an empty {} is no resume for LangGraph: it just pauses again
    ids=["empty-prefill", "empty-text", "int", "list", "dict-without-answer"],
)
def test_replay_whatsapp_empty_or_odd_resume_asks_again_then_takes_the_answer(studio_paths, no_network, resume):
    graph = studio.build("whatsapp", MemorySaver())
    run = {"configurable": {"thread_id": "studio-odd-resume"}}
    graph.invoke({"message_path": str(SAMPLES / "04-whatsapp-doubt.json")}, run)
    again = graph.invoke(Command(resume=resume), run)
    (pending,) = again["__interrupt__"]
    assert pending.value["rejected"]
    assert again.get("order_id") is None
    state = graph.invoke(Command(resume=DOUBT_ANSWER), run)
    assert state["order_id"] is not None
    assert no_network == []


def test_build_seeds_an_existing_but_empty_studio_database(studio_paths):
    db.connect(studio.DB_PATH).close()  # a first seed that failed leaves the file behind
    assert studio.DB_PATH.exists()
    studio.web_form_graph()
    conn = db.connect(studio.DB_PATH)
    try:
        assert db.catalog_rows(conn)
    finally:
        conn.close()

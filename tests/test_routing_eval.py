"""C13, C15, C23 (phase 05): the deterministic `channel_routing` evaluation, its dataset, report and gate."""

import json

from purchase_cycle import cli, router
from purchase_cycle.evaluation import routing_eval


def test_versioned_dataset_meets_its_minimums_and_matches_its_builder():
    items = routing_eval.load_dataset()
    assert len(items) >= 40
    assert items == routing_eval.build_items()
    assert len({i["id"] for i in items}) == len(items)
    routes = {i["expected"]["route"] for i in items}
    assert routes == {
        router.WEB_FORM,
        router.EMAIL,
        router.WHATSAPP_NEW,
        router.WHATSAPP_ANSWER,
        router.DUPLICATE,
        router.REJECTED,
    }
    reasons = " | ".join(i["expected"]["reason"] for i in items if i["expected"]["route"] == router.REJECTED)
    for reason in (
        "fits no channel",
        "is not valid JSON",
        "neither a web form submission nor a WhatsApp message",
        "cannot be read",
        "already stored for another customer",
    ):
        assert reason in reasons, reason
    answers = [i for i in items if i["expected"]["route"] == router.WHATSAPP_ANSWER]
    assert all(i["expected"]["thread_id"] for i in answers)


def test_eval_command_routes_every_versioned_item_and_passes_the_gate(capsys):
    assert cli.main(["eval", "--suite", "channel_routing"]) == 0
    out = capsys.readouterr().out
    n = len(routing_eval.load_dataset())
    assert f"channel_routing  mode=replay  items={n}" in out
    row = next(line for line in out.splitlines() if line.startswith("route_accuracy "))
    assert row.split()[:2] == ["route_accuracy", "100.0%"]
    for cell in (f" {n} ", " item ", ">=100.0%", " met ", "PASS"):
        assert cell in row, cell
    assert "per route:" in out
    assert "failures: 0" in out


def test_a_wrong_route_fails_the_gate_with_exit_one(tmp_path, capsys):
    items = routing_eval.load_dataset()
    items[0] = {**items[0], "expected": {**items[0]["expected"], "route": router.EMAIL}}
    path = tmp_path / "dataset.jsonl"
    path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
    assert routing_eval.evaluate("replay", "test", dataset_path=path) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: route_accuracy" in out
    assert f"  {items[0]['id']}  expected email" in out


def test_a_wrong_thread_or_reason_is_a_routing_failure():
    item = {"expected": {"route": router.WHATSAPP_ANSWER, "thread_id": "whatsapp-a", "reason": ""}}
    assert routing_eval.grade(item, router.Route(router.WHATSAPP_ANSWER, "whatsapp-a"))["correct"]
    assert not routing_eval.grade(item, router.Route(router.WHATSAPP_ANSWER, "whatsapp-b"))["correct"]
    rejected = {"expected": {"route": router.REJECTED, "thread_id": None, "reason": "is not valid JSON"}}
    assert not routing_eval.grade(rejected, router.Route(router.REJECTED, reason="fits no channel"))["correct"]

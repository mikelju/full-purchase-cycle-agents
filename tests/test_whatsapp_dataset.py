from collections import Counter

import pytest

from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.evaluation import whatsapp_dataset as wd
from purchase_cycle.evaluation.planning import read_jsonl

PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}


@pytest.fixture(scope="module")
def plan():
    return wd.build_plan()


# ---------- plan (increment 9) ----------


def test_plan_sizes_and_categories(plan):
    counts = Counter(m["category"] for m in plan)
    catalog_lines = [line for m in plan for line in m["lines"] if line["expected_sku"]]
    assert len(plan) >= 150
    assert set(counts) == set(wd.CATEGORIES) and len(wd.CATEGORIES) >= 5
    assert min(counts.values()) >= 25
    assert len(catalog_lines) >= 300
    assert len({m["id"] for m in plan}) == len(plan)
    assert len({m["message_id"] for m in plan}) == len(plan)


def test_plan_fixes_the_expected_intake_decision_and_lines(plan):
    for m in plan:
        assert m["is_order"] == (m["category"] != "not_an_order")
        assert bool(m["lines"]) == m["is_order"]
        assert m["sender"] == wd.CUSTOMER_BY_CODE[m["customer_code"]].phone
        assert m["source"] == "message"
        if not m["is_order"]:
            assert m["kind"] in wd.NOT_ORDER_KINDS


def test_plan_lines_are_labelled_by_construction(plan):
    for m in plan:
        skus = [line["expected_sku"] for line in m["lines"] if line["expected_sku"]]
        assert len(skus) == len(set(skus)), m["id"]
        for n, line in enumerate(m["lines"], start=1):
            assert line["line_id"] == f"{m['id']}-L{n}"
            assert line["location"] == "message"
            assert isinstance(line["expected_quantity"], int) and line["expected_quantity"] >= 1
            assert (line["expected_sku"] is None) == (line["trap"]["style"] == "out_of_catalog")
            if line["expected_sku"]:
                assert line["sale_unit"] == PRODUCT_BY_SKU[line["expected_sku"]].sale_unit
            if line["unit_style"] == "items_to_packs":
                assert line["trap"]["items_requested"] == line["expected_quantity"] * wd.pack_size(line["sale_unit"])
        if m["category"] == "chatty_single_line":
            assert len(m["lines"]) == 1
        if m["category"] == "words_or_dozens":
            assert {line["unit_style"] for line in m["lines"]} <= set(wd.WORD_STYLES)


def test_plan_covers_every_catalog_family_without_dominant_product(plan):
    skus = [line["expected_sku"] for m in plan for line in m["lines"] if line["expected_sku"]]
    assert {PRODUCT_BY_SKU[s].family for s in skus} == {p.family for p in PRODUCTS}
    assert max(Counter(skus).values()) <= 2


def test_plan_split_is_stratified_25_75(plan):
    for category in wd.CATEGORIES:
        splits = Counter(m["split"] for m in plan if m["category"] == category)
        assert splits == {"dev": wd.DEV_PER_CATEGORY, "test": wd.MESSAGES_PER_CATEGORY - wd.DEV_PER_CATEGORY}
    assert wd.DEV_PER_CATEGORY / wd.MESSAGES_PER_CATEGORY == 0.25


def test_plan_is_the_same_for_the_same_seed(plan):
    assert wd.build_plan() == plan
    assert wd.build_plan(wd.SEED + 1) != plan


def test_versioned_plan_matches_the_seeded_planner(plan):
    assert read_jsonl(wd.PLAN_PATH) == plan


def test_plan_command_writes_the_plan(tmp_path, monkeypatch, capsys):
    from purchase_cycle.cli import main

    monkeypatch.setattr(wd, "PLAN_PATH", tmp_path / "plan.jsonl")
    assert main(["whatsapp-dataset", "plan"]) == 0
    assert read_jsonl(tmp_path / "plan.jsonl") == wd.build_plan()
    assert "Planned" in capsys.readouterr().out

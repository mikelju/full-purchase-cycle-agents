"""C11 (phase 07): the `offer_extraction` dataset, its seeded plan, validation and second-pass review."""

from collections import Counter

import pytest

from purchase_cycle import quote_decision
from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.evaluation import offer_dataset as od
from purchase_cycle.evaluation.planning import read_jsonl

PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}


@pytest.fixture(scope="module")
def plan():
    return od.build_plan()


@pytest.fixture(scope="module")
def dataset():
    return od.load_dataset()


def test_plan_has_ten_categories_of_at_least_ten_and_a_hundred_replies(plan):
    counts = Counter(r["category"] for r in plan)
    assert len(od.CATEGORIES) == 10 and set(counts) == set(od.CATEGORIES)
    assert len(plan) >= 100 and min(counts.values()) >= 10
    assert len({r["id"] for r in plan}) == len(plan)


def test_same_seed_gives_the_same_plan_and_another_seed_a_different_one(plan):
    assert od.build_plan(od.SEED) == plan
    assert od.build_plan(od.SEED + 1) != plan


def test_plan_file_is_the_plan_of_the_seed(plan):
    assert read_jsonl(od.PLAN_PATH) == plan


def test_split_is_stratified_with_a_quarter_in_development(plan):
    for category in od.CATEGORIES:
        rows = [r for r in plan if r["category"] == category]
        assert Counter(r["split"] for r in rows) == {
            "dev": od.DEV_PER_CATEGORY,
            "test": len(rows) - od.DEV_PER_CATEGORY,
        }
    share = sum(r["split"] == "dev" for r in plan) / len(plan)
    assert 0.2 <= share <= 0.3


def test_requests_use_real_catalog_skus_and_labels_follow_the_rules(plan):
    for r in plan:
        product = PRODUCT_BY_SKU[r["sku"]]
        assert r["product"] == product.name and r["sale_unit"] == product.sale_unit
        assert r["requested_quantity"] >= 1
        full = {**dict.fromkeys(("quantity", "unit_price", "currency", "vat_included", "vat_rate"), None)}
        full |= {"lead_time_days": None, "working_days": None, **r["expected"]}
        usable, reason, net = quote_decision.assess(full)
        assert (usable, reason, net) == (r["expected_usable"], r["expected_reason"], r["expected_net_price"])


def test_every_unusable_reason_and_usable_replies_are_planned(plan):
    reasons = {r["expected_reason"] for r in plan}
    assert {None, "declined", "no_price", "currency", "no_lead_time", "vat_rate_missing"} <= reasons


def test_dataset_covers_the_plan_and_every_reply_passes_the_validation(plan, dataset):
    assert [d["id"] for d in dataset] == [p["id"] for p in plan]
    texts = od.load_texts()
    for planned in plan:
        assert od.validate_reply(planned, texts[planned["id"]]) == []
    assert all(d["dataset_version"] == od.DATASET_VERSION for d in dataset)


def test_validation_rejects_a_text_that_lacks_a_planned_fact_or_is_not_ascii(plan):
    planned = next(p for p in plan if p["category"] == "plain_full")
    ok = {"id": planned["id"], "body": " ".join(planned["must_contain"]) + " EUR price, thank you for the request."}
    assert od.validate_reply(planned, ok) == []
    missing = {"id": planned["id"], "body": "We can supply this item soon, at a good price in euros."}
    assert any("must contain" in e for e in od.validate_reply(planned, missing))
    assert any("ASCII" in e for e in od.validate_reply(planned, {**ok, "body": ok["body"] + " caf\u00e9"}))
    assert any("id" in e for e in od.validate_reply(planned, {**ok, "id": "OFF-999"}))


def test_chatty_replies_carry_their_unrelated_numbers(plan, dataset):
    chatty = [d for d in dataset if d["category"] == "chatty"]
    assert chatty and all(d["distractors"] and all(x in d["body"] for x in d["distractors"]) for d in chatty)


def test_second_pass_review_covers_every_reply_and_has_no_pending_decision(dataset):
    review = read_jsonl(od.REVIEW_PATH)
    assert {r["id"] for r in review} == {d["id"] for d in dataset}
    assert all(r["decision"] in (*od.REVIEW_DECISIONS, "agree") for r in review)
    assert not [r for r in review if r["decision"] == "pending"]
    for r in review:
        assert (r["decision"] == "agree") == (not r["disagreements"])
        if r["decision"] != "agree":
            assert r["justification"]


def test_review_disagreements_compare_only_the_graded_fields():
    row = {"id": "OFF-001", "expected": {"offers": True, "quantity": 10, "unit_price": 4.2, "currency": "EUR"}}
    read = {"offers": True, "quantity": 12, "unit_price": 4.2, "currency": "EUR", "vat_included": True}
    assert od.review_disagreements(row, read) == [{"field": "quantity", "label": 10, "read": 12}]
    assert od.review_disagreements(row, {**read, "quantity": 10}) == []
    assert od.review_disagreements(row, {**read, "quantity": 10, "unit_price": 4.204}) == []

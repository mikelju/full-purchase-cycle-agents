"""C19 (phase 11): the `response_drafting` dataset, its seeded plan and its automatic validation."""

from collections import Counter

import pytest

from purchase_cycle import customer_responses
from purchase_cycle.evaluation import response_dataset as rd
from purchase_cycle.evaluation.planning import read_jsonl
from test_sales import _incidents, _planned


@pytest.fixture(scope="module")
def plan():
    return rd.build_plan()


@pytest.fixture(scope="module")
def dataset():
    return rd.load_dataset()


def test_ten_categories_of_at_least_six_and_sixty_cases(dataset):
    counts = Counter(r["category"] for r in dataset)
    assert set(counts) == set(rd.CATEGORIES) and len(rd.CATEGORIES) == 10
    assert len(dataset) >= 60 and min(counts.values()) >= 6
    assert len({r["id"] for r in dataset}) == len(dataset)


def test_split_is_stratified_about_a_quarter_development(dataset):
    for category in rd.CATEGORIES:
        splits = Counter(r["split"] for r in dataset if r["category"] == category)
        assert splits["dev"] == rd.DEV_PER_CATEGORY and splits["test"] >= 6 - rd.DEV_PER_CATEGORY
    assert Counter(r["split"] for r in dataset) == {"dev": 20, "test": 60}


def test_same_seed_gives_the_same_plan_and_files_are_that_plan(plan):
    assert rd.build_plan(rd.SEED) == plan
    assert rd.build_plan(rd.SEED + 1) != plan
    assert read_jsonl(rd.PLAN_PATH) == plan


def test_dataset_file_is_the_build_of_the_plan(plan, dataset):
    rows, rejected = rd.build_dataset(plan)
    assert not rejected and rows == dataset


def test_every_case_equals_the_classify_and_document_rule_and_passes_validation(dataset):
    for row in dataset:
        assert rd.validate_case(row) == []
        assert rd.facts_of(row) == row["facts"]
        kinds = tuple(d["kind"] for d in row["facts"]["documents"])
        assert kinds == rd.KINDS[row["category"]]


def test_every_template_passes_the_fact_check(dataset):
    for row in dataset:
        assert customer_responses.check_response(customer_responses.fill_template(row["facts"]), row["facts"]) == []


def test_the_prompt_is_the_product_facts_prompt(dataset):
    row = dataset[0]
    assert rd.model_text(row) == customer_responses.facts_prompt(row["facts"])
    assert row["facts"]["recipient"] not in rd.model_text(row)


def test_validation_rejects_a_wrong_document_or_category(dataset):
    row = dataset[0]
    wrong = dict(row["facts"], documents=[dict(row["facts"]["documents"][0], total=1.0)])
    assert any("facts differ" in e for e in rd.validate_case(dict(row, facts=wrong)))
    assert any("do not fit" in e for e in rd.validate_case(dict(row, category="both_documents")))
    overloss = dict(row, incidents=[dict(row["incidents"][0], units=10**6)], facts=None)
    assert any("delivered" in e for e in rd.validate_case(overloss))


@pytest.mark.parametrize("category", rd.CATEGORIES)
def test_the_facts_equal_the_product_path_on_a_real_database(category, dataset, tmp_path):
    row = next(r for r in dataset if r["category"] == category)
    body = _incidents(*[(i["sku"], i["type"], i["units"]) for i in row["incidents"]])
    conn, _ = _planned(tmp_path, body, lines=tuple((d["sku"], d["quantity"]) for d in row["delivered"]))
    real = customer_responses.response_facts(conn, 1)
    mine = row["facts"]
    assert real["incidents"] == mine["incidents"] and real["net_total"] == mine["net_total"]
    strip = lambda docs: [(d["kind"], d["total"], d["lines"]) for d in docs]  # noqa: E731
    assert strip(real["documents"]) == strip(mine["documents"])


def test_review_disagreements_compare_documents_and_totals(dataset):
    row = dataset[0]
    read = {"documents": row["facts"]["documents"], "net_total": row["facts"]["net_total"]}
    assert rd.review_disagreements(row, read) == []
    assert rd.review_disagreements(row, dict(read, net_total=row["facts"]["net_total"] + 1))
    assert rd.review_disagreements(row, dict(read, documents=[]))


def test_every_blind_case_prices_every_sku_it_needs(dataset):
    blind = rd.blind_cases(dataset)
    assert len(blind) == len(dataset)
    off_note_cases = 0
    for case in blind:
        priced = {d["sku"]: d["unit_price"] for d in case["delivered"]}
        priced.update({c["sku"]: c["unit_price"] for c in case["catalog"]})
        assert {i["sku"] for i in case["incidents"]} <= set(priced), case["id"]
        assert all(isinstance(v, float) for v in priced.values())
        off_note_cases += bool(case["catalog"])
    assert off_note_cases >= 16

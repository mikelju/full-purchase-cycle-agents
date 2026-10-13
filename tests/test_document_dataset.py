"""C10 (phase 08): the `document_extraction` dataset, its seeded plan, validation, formats and second-pass review."""

from collections import Counter

import pytest

from purchase_cycle import email_order
from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.evaluation import document_dataset as dd
from purchase_cycle.evaluation.planning import read_jsonl
from purchase_cycle.suppliers import SUPPLIER_FAMILIES, SUPPLIERS

PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}


def good_body(planned):
    """A minimal text that states every labelled fact of a plain planned document."""
    head = "Invoice" if planned["kind"] == "invoice" else "Delivery note"
    rows = [f"{head} {planned['number']} dated {planned['issue_date']} from {planned['supplier_written']}"]
    currency = " Currency EUR." if planned["kind"] == "invoice" else ""
    rows.append(f"Your order {planned['reference_written']}.{currency}")
    for x in planned["lines"]:
        ident = x["sku"] if x["written_as"] == "sku" else x["product"]
        price = "" if x["unit_price"] is None else f" {x['unit_price']:.2f} {x['line_total']:.2f}"
        rows.append(f"{ident} {x['quantity']}{price}")
    return "\n".join(rows)


@pytest.fixture(scope="module")
def plan():
    return dd.build_plan()


@pytest.fixture(scope="module")
def dataset():
    return dd.load_dataset()


def test_plan_has_ten_categories_of_at_least_ten_and_a_hundred_documents(plan):
    counts = Counter(r["category"] for r in plan)
    assert len(dd.CATEGORIES) == 10 and set(counts) == set(dd.CATEGORIES)
    assert len(plan) >= 100 and min(counts.values()) >= 10
    assert len({r["id"] for r in plan}) == len(plan)
    assert {r["kind"] for r in plan} == {"invoice", "delivery_note"}


def test_same_seed_gives_the_same_plan_and_another_seed_a_different_one(plan):
    assert dd.build_plan(dd.SEED) == plan
    assert dd.build_plan(dd.SEED + 1) != plan


def test_plan_file_is_the_plan_of_the_seed(plan):
    assert read_jsonl(dd.PLAN_PATH) == plan


def test_split_is_stratified_with_a_quarter_in_development(plan):
    for category in dd.CATEGORIES:
        rows = [r for r in plan if r["category"] == category]
        assert Counter(r["split"] for r in rows) == {
            "dev": dd.DEV_PER_CATEGORY,
            "test": len(rows) - dd.DEV_PER_CATEGORY,
        }
    assert 0.2 <= sum(r["split"] == "dev" for r in plan) / len(plan) <= 0.3


def test_labels_use_real_skus_sale_units_and_suppliers_that_sell_them(plan):
    families = {(code, family) for code, family in SUPPLIER_FAMILIES}
    names = {s.code: s for s in SUPPLIERS}
    for r in plan:
        assert r["supplier_name"] == names[r["supplier_code"]].name
        assert r["po_reference"] == f"PO-{r['po_number']:04d}"
        assert names[r["supplier_code"]] is SUPPLIERS[(r["po_number"] - 1) % len(SUPPLIERS)]
        assert r["lines"]
        for line in r["lines"]:
            product = PRODUCT_BY_SKU[line["sku"]]
            assert line["sale_unit"] == product.sale_unit and line["quantity"] > 0
            assert (r["supplier_code"], product.family) in families
        if r["kind"] == "delivery_note":
            assert r["currency"] is None and all(x["unit_price"] is None for x in r["lines"])
        else:
            assert r["currency"] == "EUR" and all(x["unit_price"] > 0 for x in r["lines"])


def test_every_category_has_the_traps_it_names(plan):
    by = {c: [r for r in plan if r["category"] == c] for c in dd.CATEGORIES}
    assert all(r["kind"] == "invoice" for r in by["clean_invoice"]) and len(by["clean_invoice"][0]["lines"]) >= 1
    assert all(r["kind"] == "delivery_note" for r in by["delivery_note"])
    assert all(len(r["lines"]) >= 3 for r in by["several_lines"])
    assert all(r["distractors"] for r in by["vat_totals"] + by["extra_charge"] + by["chatty"])
    assert len({r["reference_written"] for r in by["reference_forms"]}) >= 8
    assert all(any(x["written_unit"] for x in r["lines"]) for r in by["other_unit"])


def test_a_share_of_documents_is_planned_as_pdf_and_excel(plan):
    formats = Counter(r["format"] for r in plan)
    assert set(formats) == {"txt", "pdf", "xlsx"} and formats["pdf"] >= 10 and formats["xlsx"] >= 10


def test_dataset_covers_the_plan_and_every_document_passes_the_validation(plan, dataset):
    assert [d["id"] for d in dataset] == [p["id"] for p in plan]
    texts = dd.load_texts()
    for planned in plan:
        assert dd.validate_document(planned, texts[planned["id"]]) == []
    assert all(d["dataset_version"] == dd.DATASET_VERSION for d in dataset)


def test_validation_rejects_missing_facts_invented_skus_and_non_ascii(plan):
    planned = next(p for p in plan if p["category"] == "clean_invoice")
    good = good_body(planned)
    assert dd.validate_document(planned, {"id": planned["id"], "body": good}) == []
    other = next(p for p in PRODUCTS if p.sku not in {x["sku"] for x in planned["lines"]})
    for body, word in (
        ("Invoice for some goods, thank you very much for the order we got.", "must contain"),
        (good + f"\n{other.sku} 1 1.00 1.00", "not in the label"),
        (good + " caf\u00e9", "ASCII"),
    ):
        assert any(word in e for e in dd.validate_document(planned, {"id": planned["id"], "body": body}))
    assert any("id" in e for e in dd.validate_document(planned, {"id": "DOC-999", "body": good}))


def test_validation_rejects_mutated_numbers_and_extra_lines_written_by_name(plan):
    planned = next(p for p in plan if p["category"] == "clean_invoice" and p["lines"][0]["written_as"] == "sku")
    line = planned["lines"][0]
    good = good_body(planned)
    quantity, price = str(line["quantity"]), f"{line['unit_price']:.2f}"
    other = next(p for p in PRODUCTS if p.sku not in {x["sku"] for x in planned["lines"]})
    bodies = (
        good.replace(f"{line['sku']} {quantity} ", f"{line['sku']} {quantity}0 ", 1),
        good.replace(f" {price} ", f" 1{price} ", 1),
        good.replace(planned["number"], planned["number"] + "0", 1),
        good + "\n" + other.name + " 1 1.00 1.00",
    )
    for body in bodies:
        assert body != good
        assert dd.validate_document(planned, {"id": planned["id"], "body": body}), body[:80]


def test_delivery_notes_state_no_price(plan):
    planned = next(p for p in plan if p["kind"] == "delivery_note")
    body = good_body(planned) + "\nTotal 12.50 EUR"
    assert any("price" in e for e in dd.validate_document(planned, {"id": planned["id"], "body": body}))


def test_rendered_pdf_and_excel_files_exist_and_read_back_the_same_facts(plan, dataset):
    rendered = [d for d in dataset if d["format"] != "txt"]
    assert len(rendered) == sum(p["format"] != "txt" for p in plan)
    for d in rendered:
        path = dd.DATASET_DIR / d["file"]
        assert path.suffix == f".{d['format']}" and path.exists()
        text = dd.document_text(d)
        assert text == (email_order.pdf_text if d["format"] == "pdf" else email_order.xlsx_text)(path.read_bytes())
        assert dd.validate_document(d, {"id": d["id"], "body": text}) == []


def test_renderers_are_deterministic():
    body = "INVOICE INV-1\nSKU  QTY  PRICE\nGLV-NIT-XS  10  6.90\n"
    assert dd.render_pdf(body) == dd.render_pdf(body)
    assert dd.render_xlsx(body) == dd.render_xlsx(body)
    assert "6.90" in email_order.xlsx_text(dd.render_xlsx(body))


def test_second_pass_review_covers_every_document_and_has_no_pending_decision(dataset):
    if not dd.REVIEW_PATH.exists():
        pytest.skip("second pass pending: the coordinator's clean-context review is not recorded yet")
    review = read_jsonl(dd.REVIEW_PATH)
    assert {r["id"] for r in review} == {d["id"] for d in dataset}
    assert not [r for r in review if r["decision"] == "pending"]
    for r in review:
        assert (r["decision"] == "agree") == (not r["disagreements"])
        if r["decision"] != "agree":
            assert r["justification"]


def test_review_disagreements_compare_kind_reference_currency_and_lines():
    row = {
        "id": "DOC-001",
        "kind": "invoice",
        "po_reference": "PO-0007",
        "currency": "EUR",
        "lines": [{"sku": "GLV-NIT-XS", "quantity": 10, "unit_price": 6.9}],
    }
    read = {
        "kind": "invoice",
        "po_reference": "po 7",
        "currency": "EUR",
        "lines": [{"product": "glv-nit-xs", "quantity": 10, "unit_price": 6.904}],
    }
    assert dd.review_disagreements(row, read) == []
    bad = {**read, "kind": "delivery_note", "lines": [{"product": "GLV-NIT-XS", "quantity": 12, "unit_price": 6.9}]}
    fields = {d["field"] for d in dd.review_disagreements(row, bad)}
    assert fields == {"kind", "lines"}
    missing = dd.review_disagreements(row, {**read, "lines": []})
    assert missing and missing[0]["field"] == "lines"

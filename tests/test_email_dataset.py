import copy
import csv
from collections import Counter

import pytest

from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.email_order import parse_email
from purchase_cycle.evaluation import email_dataset as ed
from purchase_cycle.evaluation.planning import read_jsonl
from purchase_cycle.evaluation.stats import wilson_interval

PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}


@pytest.fixture(scope="module")
def plan():
    return ed.build_plan()


def _product_text(line):
    style = line["trap"]["style"]
    if style == "out_of_catalog":
        return line["trap"]["requested_item"]
    if style == "plain":
        return PRODUCT_BY_SKU[line["expected_sku"]].name
    return f"placeholder product {line['line_id']}"


def _line_text(planned, line):
    product = _product_text(line)
    if planned["category"] in ed.TABLE_CATEGORIES:
        return product
    quantity = line["expected_quantity"]
    return {
        "sale_units": f"{quantity} x {product}",
        "items_to_packs": f"{line['trap'].get('items_requested')} pieces of {product}",
        "quantity_words": f"quantity in words of {product}",
        "dozen": f"a dozen lots of {product}",
        "half_dozen": f"half a dozen of {product}",
        "couple": f"a couple of {product}",
    }[line["unit_style"]]


def placeholder_text(planned):
    """A text that passes validation for any planned email; stands in for the agent's writing."""
    lines = {line["line_id"]: _line_text(planned, line) for line in planned["lines"]}
    category = planned["category"]
    if category == "body_list":
        body = "Hello,\n\nPlease send:\n" + "\n".join(f"- {t}" for t in lines.values()) + "\n\nThanks"
    elif category == "body_conversational":
        body = (
            "Hello team, for our clinic this week and the next one we would need, if possible, "
            + " and also ".join(lines.values())
            + ". Thanks a lot and kind regards."
        )
    elif category == "not_an_order":
        body = f"Hello, a question about delivery times for reference {planned['id']}.\n\nRegards"
    else:
        body = f"Hello, our order {planned['id']} is attached.\n\nRegards"
    return {"id": planned["id"], "subject": f"Message {planned['id']}", "body": body, "lines": lines}


def one_per_layout(plan):
    seen, picked = set(), []
    for planned in plan:
        if planned["layout"] not in seen or planned["category"] == "body_conversational" and len(picked) < 9:
            seen.add(planned["layout"])
            picked.append(planned)
    return picked


# ---------- plan (increment 5) ----------


def test_plan_sizes_and_categories(plan):
    lines = [line for e in plan for line in e["lines"]]
    counts = Counter(e["category"] for e in plan)
    assert len(plan) >= 300
    assert set(counts) == set(ed.CATEGORIES)
    assert min(counts.values()) >= 50
    assert len(lines) >= 800
    assert all(e["is_order"] == (e["category"] != "not_an_order") for e in plan)
    assert all(e["lines"] for e in plan if e["is_order"])
    assert len({e["id"] for e in plan}) == len(plan)


def test_plan_covers_catalog_without_dominant_product(plan):
    skus = [line["expected_sku"] for e in plan for line in e["lines"] if line["expected_sku"]]
    assert {PRODUCT_BY_SKU[s].family for s in skus} == {p.family for p in PRODUCTS}
    assert all(s in PRODUCT_BY_SKU for s in skus)
    assert max(Counter(skus).values()) <= ed.MAX_PRODUCT_SHARE * len(skus)


def test_plan_has_no_repeated_sku_inside_an_email(plan):
    for e in plan:
        skus = [line["expected_sku"] for line in e["lines"] if line["expected_sku"]]
        assert len(skus) == len(set(skus)), e["id"]


def test_plan_trap_shares_are_fixed_in_every_order_category(plan):
    for category in ed.ORDER_CATEGORIES:
        lines = [line for e in plan if e["category"] == category for line in e["lines"]]
        styles = Counter(line["trap"]["style"] for line in lines)
        for trap, share in ed.TRAP_SHARES:
            assert styles[trap] == round(share * len(lines)), (category, trap)
            assert styles[trap] > 0


def test_plan_lines_are_labelled_by_construction(plan):
    for e in plan:
        customer = ed.CUSTOMER_BY_CODE[e["customer_code"]]
        assert e["sender"] == customer.email
        for line in e["lines"]:
            assert isinstance(line["expected_quantity"], int) and line["expected_quantity"] >= 1
            assert (line["expected_sku"] is None) == (line["trap"]["style"] == "out_of_catalog")
            assert line["location"] == (e["attachment"] or "body")
            if line["unit_style"] == "items_to_packs":
                pack = ed.pack_size(line["sale_unit"])
                assert line["trap"]["items_requested"] == line["expected_quantity"] * pack
            if line["trap"]["style"] == "near_miss":
                confusable = PRODUCT_BY_SKU[line["trap"]["confusable_with"]]
                assert confusable.family == PRODUCT_BY_SKU[line["expected_sku"]].family
        if e["category"] in ed.TABLE_CATEGORIES:
            assert {line["unit_style"] for line in e["lines"]} <= {"sale_units", "items_to_packs"}


def test_plan_split_is_stratified_by_email(plan):
    for category in ed.CATEGORIES:
        splits = Counter(e["split"] for e in plan if e["category"] == category)
        assert splits == {"dev": ed.DEV_PER_CATEGORY, "test": ed.EMAILS_PER_CATEGORY - ed.DEV_PER_CATEGORY}


def test_plan_is_the_same_for_the_same_seed(plan):
    assert ed.build_plan() == plan
    assert ed.build_plan(ed.SEED + 1) != plan


def test_versioned_plan_matches_the_seeded_planner(plan):
    assert ed.read_jsonl(ed.PLAN_PATH) == plan


# ---------- validation and rendering (increment 6) ----------


def test_placeholder_texts_pass_validation_and_rendering(plan):
    picked = one_per_layout(plan)
    assert {e["layout"] for e in picked} == {layout for layouts in ed.LAYOUTS.values() for layout in layouts}
    rows, files, rejected = ed.build_dataset(picked, {e["id"]: placeholder_text(e) for e in picked})
    assert rejected == {}
    assert [r["id"] for r in rows] == [e["id"] for e in picked]
    for planned in picked:
        email = parse_email(files[planned["id"]])
        assert email["sender"] == planned["sender"]
        assert [a["name"] for a in email["attachments"]] == ([planned["attachment"]] if planned["attachment"] else [])


def test_two_renders_give_identical_bytes_and_text(plan):
    for planned in one_per_layout(plan):
        text = placeholder_text(planned)
        first, second = ed.render_email(planned, text), ed.render_email(planned, copy.deepcopy(text))
        assert first == second, planned["layout"]
        assert parse_email(first) == parse_email(second)


def test_table_attachments_carry_the_planned_quantities(plan):
    for planned in one_per_layout(plan):
        if planned["category"] not in ed.TABLE_CATEGORIES:
            continue
        text = placeholder_text(planned)
        attachment = parse_email(ed.render_email(planned, text))["attachments"][0]["text"]
        for _product, quantity, unit in ed._table_rows(planned, text):
            assert ed.has_number(attachment, quantity) and unit in attachment


def _first(plan, category, **where):
    return copy.deepcopy(
        next(
            e
            for e in plan
            if e["category"] == category and all(any(line.get(k) == v for line in e["lines"]) for k, v in where.items())
        )
    )


def test_validation_rejects_a_missing_sku(plan):
    planned = _first(plan, "body_list")
    text = placeholder_text(planned)
    planned["lines"][0]["expected_sku"] = "NOT-A-SKU"
    _, _, rejected = ed.build_dataset([planned], {planned["id"]: text})
    assert any("does not exist" in r for r in rejected[planned["id"]])


@pytest.mark.parametrize("quantity", [0, -3, 2.5])
def test_validation_rejects_a_non_positive_or_fractional_quantity(plan, quantity):
    planned = _first(plan, "xlsx_attachment")
    planned["lines"][0]["expected_quantity"] = quantity
    _, _, rejected = ed.build_dataset([planned], {planned["id"]: placeholder_text(planned)})
    assert any("not a positive whole number" in r for r in rejected[planned["id"]])


def test_validation_rejects_a_duplicate_text_after_normalisation(plan):
    first, second = [copy.deepcopy(e) for e in plan if e["category"] == "not_an_order"][:2]
    text_a = placeholder_text(first)
    text_b = dict(placeholder_text(second), body=text_a["body"].upper().replace(".", " !"))
    _, _, rejected = ed.build_dataset([first, second], {first["id"]: text_a, second["id"]: text_b})
    assert first["id"] not in rejected
    assert rejected[second["id"]] == [f"duplicates {first['id']} after normalisation"]


def test_validation_rejects_a_planned_line_missing_from_the_body(plan):
    planned = _first(plan, "body_conversational")
    text = placeholder_text(planned)
    lid = planned["lines"][0]["line_id"]
    text["body"] = text["body"].replace(text["lines"][lid], "something else")
    _, _, rejected = ed.build_dataset([planned], {planned["id"]: text})
    assert any(lid in r and "missing" in r for r in rejected[planned["id"]])


def test_rendered_check_rejects_a_planned_line_missing_from_the_attachment(plan):
    planned = _first(plan, "pdf_attachment")
    text = placeholder_text(planned)
    lid = planned["lines"][0]["line_id"]
    other = copy.deepcopy(planned)
    other["lines"][0]["line_id"] = "SWAPPED"
    data = ed.render_email(other, dict(text, lines={**text["lines"], "SWAPPED": "a different product text"}))
    errors = ed.check_rendered(planned, text, data)
    assert errors == [f"{lid}: planned line missing from the rendered {planned['attachment']}"]


@pytest.mark.parametrize(
    "category,change,reason",
    [
        ("body_list", lambda p, t: t.update(body=" ".join(t["body"].split())), "own body line"),
        ("body_conversational", lambda p, t: t.update(body=" ".join(t["lines"].values())), "characters of context"),
        ("txt_attachment", lambda p, t: t.update(body=t["body"] + "\n" + next(iter(t["lines"].values()))), "repeats"),
        ("pdf_attachment", lambda p, t: t.update(body="x" * (ed.MAX_SHORT_BODY_CHARS + 1)), "body longer"),
        ("not_an_order", lambda p, t: t.update(lines={"EXTRA-L1": "10 boxes"}), "do not match the planned lines"),
    ],
)
def test_validation_rejects_a_category_rule_failure(plan, category, change, reason):
    planned = _first(plan, category)
    text = placeholder_text(planned)
    change(planned, text)
    _, _, rejected = ed.build_dataset([planned], {planned["id"]: text})
    assert any(reason in r for r in rejected[planned["id"]]), rejected


@pytest.mark.parametrize(
    "unit_style,bad_text",
    [
        ("sale_units", "some boxes of gauze"),
        ("quantity_words", "{quantity} boxes of gauze"),
        ("dozen", "{quantity} boxes of gauze"),
        ("items_to_packs", "{quantity} boxes of gauze"),
    ],
)
def test_validation_rejects_a_line_whose_quantity_expression_breaks_its_rule(plan, unit_style, bad_text):
    planned = _first(plan, "body_list", unit_style=unit_style)
    line = next(line for line in planned["lines"] if line["unit_style"] == unit_style)
    errors = ed.validate_line(planned, line, bad_text.format(quantity=line["expected_quantity"]))
    assert errors and line["line_id"] in errors[0]


def test_validation_rejects_traps_that_name_the_catalog_product(plan):
    planned = _first(plan, "pdf_attachment")
    for line in (line for e in plan if e["category"] == "pdf_attachment" for line in e["lines"]):
        if line["trap"]["style"] == "typo":
            name = PRODUCT_BY_SKU[line["expected_sku"]].name
            assert any("exact catalog name" in e for e in ed.validate_line(planned, line, name))
            break


# ---------- versioned texts, emails and dataset (increment 7) ----------


def test_versioned_dataset_is_the_build_of_the_versioned_texts(plan):
    rows, files, rejected = ed.build_dataset(plan, ed.load_texts())
    assert rejected == {}
    assert ed.load_dataset() == rows
    assert sorted(p.name for p in ed.EMAILS_DIR.glob("*.eml")) == sorted(f"{i}.eml" for i in files)
    for email_id, data in files.items():
        assert (ed.EMAILS_DIR / f"{email_id}.eml").read_bytes() == data, email_id
    counts = Counter((row["category"], row["split"]) for row in rows)
    assert all(counts[(c, "dev")] == ed.DEV_PER_CATEGORY for c in ed.CATEGORIES)
    assert all(counts[(c, "test")] == ed.EMAILS_PER_CATEGORY - ed.DEV_PER_CATEGORY for c in ed.CATEGORIES)


# ---------- second-pass review (increment 8) ----------


def _labelled_email():
    lines = [
        {
            "line_id": "E-L1",
            "text": "3 boxes of nitrile gloves size M",
            "expected_sku": "GLV-NIT-M",
            "expected_quantity": 3,
        },
        {"line_id": "E-L2", "text": "10 patient lifts", "expected_sku": None, "expected_quantity": 10},
    ]
    return {"id": "E", "is_order": True, "lines": lines}


def _reading(**changes):
    lines = [
        {"text": "nitrile gloves M 3 boxes", "sku": "GLV-NIT-M", "quantity": 3},
        {"text": "10 patient lifts", "sku": None, "quantity": 10},
    ]
    return {"id": "E", "is_order": True, "lines": lines, "notes": ""} | changes


def test_review_finds_no_disagreement_when_the_reading_matches_the_labels():
    assert ed.review_disagreements(_labelled_email(), _reading()) == []


def test_review_reports_each_kind_of_disagreement():
    email = _labelled_email()
    wrong_sku = _reading(
        lines=[{"text": "nitrile gloves M", "sku": "GLV-NIT-L", "quantity": 4}, _reading()["lines"][1]]
    )
    assert [(d["field"], d["label"], d["read"]) for d in ed.review_disagreements(email, wrong_sku)] == [
        ("sku", "GLV-NIT-M", "GLV-NIT-L"),
        ("quantity", 3, 4),
    ]
    missing_and_extra = _reading(
        is_order=False, lines=[_reading()["lines"][0], {"text": "a box of plasters", "sku": None, "quantity": 1}]
    )
    found = [(d["field"], d.get("line_id")) for d in ed.review_disagreements(email, missing_and_extra)]
    assert found == [("is_order", None), ("line", "E-L2"), ("line", None)]


def test_review_keeps_earlier_decisions_and_leaves_new_disagreements_pending():
    email = _labelled_email()
    disagreeing = _reading(is_order=False)
    previous = [{"id": "E", "decision": "annotator_wrong", "justification": "the email says order"}]
    assert ed.build_review([email], [disagreeing], previous)[0]["decision"] == "annotator_wrong"
    assert ed.build_review([email], [disagreeing], [])[0]["decision"] == "pending"
    assert ed.build_review([email], [_reading()], previous)[0]["decision"] == "agree"


def test_versioned_second_pass_review_covers_every_email_and_decides_every_disagreement():
    dataset = {e["id"]: e for e in ed.load_dataset()}
    records = read_jsonl(ed.REVIEW_PATH)
    assert [r["id"] for r in records] == list(dataset)
    for record in records:
        reading = {"is_order": record["read"]["is_order"], "lines": record["read"]["lines"]}
        assert ed.review_disagreements(dataset[record["id"]], reading) == record["disagreements"]
        assert record["decision"] in ed.REVIEW_DECISIONS
        assert (record["decision"] == "agree") == (record["disagreements"] == [])
        assert record["decision"] == "agree" or record["justification"].strip()


# ---------- owner audit (increment 9) ----------


def _audit_file(path, wrong: int, pending: int = 0):
    rows = []
    for i in range(ed.AUDIT_SIZE):
        verdict = "" if i < pending else "wrong" if i < pending + wrong else "ok"
        rows.append(dict.fromkeys(ed.AUDIT_COLUMNS, "") | {"id": f"EML-{i:04d}", "verdict": verdict})
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ed.AUDIT_COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.mark.parametrize(("wrong", "code"), [(0, 0), (1, 0), (2, 1)])
def test_audit_report_passes_with_at_most_one_wrong_label(tmp_path, capsys, wrong, code):
    assert ed.MAX_WRONG == 1
    assert ed.report_audit(_audit_file(tmp_path / "audit.csv", wrong)) == code
    low, high = wilson_interval(wrong, ed.AUDIT_SIZE)
    out = capsys.readouterr().out
    assert f"wrong labels={wrong} " in out
    assert f"95% CI [{low:.1%}, {high:.1%}]" in out


def test_audit_report_refuses_rows_without_a_verdict(tmp_path):
    assert ed.report_audit(_audit_file(tmp_path / "audit.csv", 0, pending=1)) == 2


def test_audit_create_samples_forty_seeded_emails_and_never_overwrites(tmp_path):
    path = tmp_path / "audit.csv"
    assert ed.create_audit(path) == 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    dataset = {e["id"]: e for e in ed.load_dataset()}
    assert len(rows) == ed.AUDIT_SIZE == 40
    assert [r["id"] for r in rows] == sorted({r["id"] for r in rows})
    assert all(r["verdict"] == "" and r["email_text"].startswith("Subject: ") for r in rows)
    assert all(r["is_order"] == ("yes" if dataset[r["id"]]["is_order"] else "no") for r in rows)
    first = path.read_bytes()
    assert ed.create_audit(path) == 1 and path.read_bytes() == first
    second = tmp_path / "again.csv"
    ed.create_audit(second)
    assert second.read_bytes() == first
    # Git stores the versioned file with LF endings, so compare its parsed rows, not its bytes.
    with ed.AUDIT_PATH.open(encoding="utf-8-sig", newline="") as fh:
        versioned = list(csv.DictReader(fh, delimiter=";"))
    assert versioned == rows

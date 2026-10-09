import copy
import json
import shutil
from collections import Counter

import pytest

from purchase_cycle import db
from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.evaluation import whatsapp_dataset as wd
from purchase_cycle.evaluation.planning import read_jsonl, write_jsonl
from purchase_cycle.whatsapp_order import WhatsAppMessage, read_whatsapp

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


# ---------- validation and build (increment 10) ----------

UNITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen".split()
UNITS += "seventeen eighteen nineteen".split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def words(n):
    if n >= 100:
        rest = n % 100
        return f"{UNITS[n // 100]} hundred" + (f" and {words(rest)}" if rest else "")
    if n >= 20:
        return TENS[n // 10 - 2] + (f"-{UNITS[n % 10]}" if n % 10 else "")
    return UNITS[n]


def _product_text(line):
    style = line["trap"]["style"]
    if style == "out_of_catalog":
        return line["trap"]["requested_item"]
    if style == "plain":
        return PRODUCT_BY_SKU[line["expected_sku"]].name
    return f"placeholder product {line['line_id'].lower()}"


def _line_text(line):
    product, quantity = _product_text(line), line["expected_quantity"]
    return {
        "sale_units": f"{quantity} x {product}",
        "items_to_packs": f"{line['trap'].get('items_requested')} pieces of {product}",
        "quantity_words": f"{words(quantity)} lots of {product}",
        "dozen": f"{words(quantity // 12)} dozen lots of {product}",
        "half_dozen": f"half a dozen of {product}",
        "couple": f"a couple of {product}",
    }[line["unit_style"]]


def placeholder_text(planned):
    """A text that passes validation for any planned message; stands in for the agents' writing."""
    lines = {line["line_id"]: _line_text(line) for line in planned["lines"]}
    category = planned["category"]
    if category == "short_list":
        body = "Hi, order please:\n" + "\n".join(lines.values()) + "\nThanks"
    elif category == "not_an_order":
        body = f"Hello, a {planned['kind']} about our account, reference {planned['id']}."
    else:
        body = f"Hi there from the clinic, reference {planned['id']}, we need " + " and ".join(lines.values()) + "."
    return {"id": planned["id"], "body": body, "lines": lines}


def _first(plan, category, **where):
    return copy.deepcopy(
        next(
            m
            for m in plan
            if m["category"] == category and all(any(line.get(k) == v for line in m["lines"]) for k, v in where.items())
        )
    )


def test_placeholder_texts_pass_validation_and_render_readable_messages(plan):
    rows, files, rejected = wd.build_dataset(plan, {m["id"]: placeholder_text(m) for m in plan})
    assert rejected == {}
    assert [r["id"] for r in rows] == [m["id"] for m in plan]
    for planned, row in zip(plan, rows, strict=True):
        message = WhatsAppMessage.model_validate(json.loads(files[planned["id"]]))
        assert (message.message_id, message.sender, message.type) == (planned["message_id"], planned["sender"], "text")
        assert message.text.body == row["body"]
        assert row["file"] == f"messages/{planned['id']}.json"
        assert all(line["text"] for line in row["lines"])


def test_a_rendered_message_is_read_by_the_whatsapp_intake(plan, tmp_path):
    planned = plan[0]
    path = tmp_path / "m.json"
    path.write_bytes(wd.render_message(planned, placeholder_text(planned)))
    conn = db.connect(tmp_path / "shop.db")
    db.seed(conn)
    received = read_whatsapp(path, conn)
    conn.close()
    assert received["customer"]["code"] == planned["customer_code"]
    assert received["body"] == placeholder_text(planned)["body"]


def test_validation_rejects_a_planned_line_missing_from_the_message(plan):
    planned = _first(plan, "one_sentence_lines")
    text = placeholder_text(planned)
    lid = planned["lines"][0]["line_id"]
    text["body"] = text["body"].replace(text["lines"][lid], "something else")
    _, _, rejected = wd.build_dataset([planned], {planned["id"]: text})
    assert f"{lid}: planned line missing from the message" in rejected[planned["id"]]


@pytest.mark.parametrize(
    "unit_style,bad_text",
    [
        ("sale_units", "some boxes of gauze"),
        ("quantity_words", "{quantity} boxes of gauze"),
        ("dozen", "{quantity} boxes of gauze"),
        ("couple", "{quantity} boxes of gauze"),
        ("items_to_packs", "{quantity} boxes of gauze"),
    ],
)
def test_validation_rejects_a_line_whose_quantity_breaks_its_rule(plan, unit_style, bad_text):
    line = next(line for m in plan for line in m["lines"] if line["unit_style"] == unit_style)
    errors = wd.validate_line(line, bad_text.format(quantity=line["expected_quantity"]))
    assert errors and line["line_id"] in errors[0]


def test_validation_rejects_a_quantity_the_text_does_not_state(plan):
    line = next(line for m in plan for line in m["lines"] if line["unit_style"] == "half_dozen")
    assert wd.validate_line(line, "half a dozen of gauze") == []
    assert any("does not state" in e for e in wd.validate_line(line, "half of the dozen boxes of gauze, and 3 more"))


def test_validation_rejects_a_duplicate_text_after_normalisation(plan):
    first, second = [copy.deepcopy(m) for m in plan if m["category"] == "not_an_order"][:2]
    text_a = placeholder_text(first)
    text_b = dict(placeholder_text(second), body=text_a["body"].upper().replace(".", " !"))
    _, _, rejected = wd.build_dataset([first, second], {first["id"]: text_a, second["id"]: text_b})
    assert first["id"] not in rejected
    assert rejected[second["id"]] == [f"duplicates {first['id']} after normalisation"]


@pytest.mark.parametrize("where", ["body", "line"])
def test_validation_rejects_non_ascii_text(plan, where):
    planned = _first(plan, "chatty_single_line")
    text = placeholder_text(planned)
    lid = planned["lines"][0]["line_id"]
    if where == "body":
        text["body"] += " \U0001f44d"
    else:
        accented = text["lines"][lid] + " mañana"
        text["body"] = text["body"].replace(text["lines"][lid], accented)
        text["lines"][lid] = accented
    _, _, rejected = wd.build_dataset([planned], {planned["id"]: text})
    assert any("plain ASCII" in r for r in rejected[planned["id"]]), rejected


@pytest.mark.parametrize(
    "category,change,reason",
    [
        ("short_list", lambda t: t.update(body=" ".join(t["body"].split())), "own message line"),
        ("chatty_single_line", lambda t: t.update(body=next(iter(t["lines"].values()))), "characters of context"),
        ("one_sentence_lines", lambda t: t.update(body=t["body"].replace(" and ", "\n", 1)), "one line"),
        ("not_an_order", lambda t: t.update(lines={"EXTRA-L1": "10 boxes"}), "do not match the planned lines"),
        ("words_or_dozens", lambda t: t.update(body=""), "body must have"),
    ],
)
def test_validation_rejects_a_category_rule_failure(plan, category, change, reason):
    planned = _first(plan, category)
    text = placeholder_text(planned)
    change(text)
    _, _, rejected = wd.build_dataset([planned], {planned["id"]: text})
    assert any(reason in r for r in rejected[planned["id"]]), rejected


def test_validation_rejects_traps_that_name_the_catalog_product(plan):
    line = next(line for m in plan for line in m["lines"] if line["trap"]["style"] == "typo")
    name = PRODUCT_BY_SKU[line["expected_sku"]].name
    text = _line_text(line).replace(_product_text(line), name)
    assert any("exact catalog name" in e for e in wd.validate_line(line, text))


def _use_folder(monkeypatch, folder):
    monkeypatch.setattr(wd, "PLAN_PATH", folder / "plan.jsonl")
    monkeypatch.setattr(wd, "TEXTS_DIR", folder / "texts")
    monkeypatch.setattr(wd, "MESSAGES_DIR", folder / "messages")
    monkeypatch.setattr(wd, "DATASET_PATH", folder / "dataset.jsonl")


def _write_texts(folder, plan):
    for category in wd.CATEGORIES:
        rows = [placeholder_text(m) for m in plan if m["category"] == category]
        write_jsonl(folder / "texts" / f"{category}.jsonl", rows)


def test_build_gives_identical_bytes_over_two_builds(plan, tmp_path, monkeypatch, capsys):
    from purchase_cycle.cli import main

    _use_folder(monkeypatch, tmp_path)
    write_jsonl(tmp_path / "plan.jsonl", plan)
    _write_texts(tmp_path, plan)
    snapshots = []
    for _ in range(2):
        assert main(["whatsapp-dataset", "build"]) == 0
        files = [*sorted(tmp_path.joinpath("messages").glob("*.json")), tmp_path / "dataset.jsonl"]
        snapshots.append({p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in files})
        shutil.rmtree(tmp_path / "messages")
        (tmp_path / "dataset.jsonl").unlink()
    assert snapshots[0] == snapshots[1]
    assert len(snapshots[0]) == len(plan) + 1
    assert f"Built {len(plan)} messages" in capsys.readouterr().out


def test_build_refuses_and_writes_nothing_when_a_text_is_rejected(plan, tmp_path, monkeypatch, capsys):
    from purchase_cycle.cli import main

    _use_folder(monkeypatch, tmp_path)
    write_jsonl(tmp_path / "plan.jsonl", plan)
    _write_texts(tmp_path, plan[1:])
    assert main(["whatsapp-dataset", "build"]) == 1
    assert not (tmp_path / "dataset.jsonl").exists()
    assert not (tmp_path / "messages").exists()
    assert f"1 of {len(plan)} messages rejected" in capsys.readouterr().out


def test_check_command_reports_the_rejected_messages_of_a_batch(plan, tmp_path, monkeypatch, capsys):
    from purchase_cycle.cli import main

    _use_folder(monkeypatch, tmp_path)
    write_jsonl(tmp_path / "plan.jsonl", plan)
    good, bad = (placeholder_text(m) for m in plan[:2])
    bad["body"] = bad["body"] + " café"
    write_jsonl(tmp_path / "batch.jsonl", [good, bad])
    assert main(["whatsapp-dataset", "check", str(tmp_path / "batch.jsonl")]) == 1
    out = capsys.readouterr().out
    assert f"{plan[1]['id']}: " in out and f"{plan[0]['id']}: " not in out
    assert "2 messages checked, 1 rejected" in out


def test_versioned_dataset_is_the_build_of_the_versioned_texts(tmp_path, monkeypatch, capsys):
    from purchase_cycle.cli import main

    versioned = wd.DATASET_DIR
    _use_folder(monkeypatch, tmp_path)
    shutil.copy(versioned / "plan.jsonl", tmp_path / "plan.jsonl")
    shutil.copytree(versioned / "texts", tmp_path / "texts")
    assert main(["whatsapp-dataset", "build"]) == 0
    built = sorted(p.name for p in (tmp_path / "messages").glob("*.json"))
    assert sorted(p.name for p in (versioned / "messages").glob("*.json")) == built
    for name in built:
        assert (versioned / "messages" / name).read_bytes() == (tmp_path / "messages" / name).read_bytes(), name
    assert (versioned / "dataset.jsonl").read_bytes() == (tmp_path / "dataset.jsonl").read_bytes()
    rows = wd.load_dataset(versioned / "dataset.jsonl")
    counts = Counter((row["category"], row["split"]) for row in rows)
    assert len(rows) == len(read_jsonl(versioned / "plan.jsonl"))
    assert all(counts[(c, "dev")] == wd.DEV_PER_CATEGORY for c in wd.CATEGORIES)
    assert all(counts[(c, "test")] == wd.MESSAGES_PER_CATEGORY - wd.DEV_PER_CATEGORY for c in wd.CATEGORIES)

import copy
import csv
from argparse import Namespace
from collections import Counter

import pytest

from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.clarification import candidate_search
from purchase_cycle.evaluation import clarification_dataset as cd
from purchase_cycle.evaluation.planning import read_jsonl, write_jsonl
from purchase_cycle.evaluation.stats import wilson_interval
from purchase_cycle.quantities import NUMBER_WORDS


def test_ambiguity_sets_are_what_their_hint_reaches():
    sets = cd.ambiguity_sets()
    assert len(sets) >= 50
    for chosen in sets:
        found = [c["sku"] for c in candidate_search(chosen["hint"], cd.CATALOG, limit=len(cd.CATALOG))]
        assert found == chosen["candidates"] and 2 <= len(found) <= 6


def test_detection_plan_meets_the_minimums():
    plan = cd.build_detection_plan()
    lines = [line for order in plan for line in order["lines"]]
    assert len(plan) >= 200 and len(lines) >= 500
    assert Counter(order["channel"] for order in plan) == {"web_form": 100, "email": 100}
    types = Counter(t for line in lines for t in line["expected_doubts"])
    assert min(types[t] for t in ("ambiguous", "unknown", "quantity")) >= 60
    no_doubt = [order for order in plan if not any(line["expected_doubts"] for line in order["lines"])]
    assert len(no_doubt) >= 60
    assert all(order["has_doubt"] == bool(order not in no_doubt) for order in plan)
    assert len({order["id"] for order in plan}) == len(plan)
    assert len({line["line_id"] for line in lines}) == len(lines)


def test_detection_lines_carry_what_the_writer_needs():
    for order in cd.build_detection_plan():
        for line in order["lines"]:
            kind = line["kind"]
            assert (line["expected_sku"] is None) == (kind in ("ambiguous", "unknown"))
            assert bool(line["candidates"]) == (kind == "ambiguous")
            assert (kind == "unknown") == ("requested_item" in line)
            assert (line["quantity"] is None) == (kind == "unsupported")
            if kind == "over_ceiling":
                assert line["quantity"] > 500
            if order["channel"] == "email":
                assert line["location"] == "body" and line["unit_style"]
                assert kind != "unsupported" or line["unit_style"] == "unstated"
            else:
                assert kind != "unsupported" and "unit_style" not in line


def test_detection_split_is_stratified_by_channel_and_doubt():
    plan = cd.build_detection_plan()
    strata = Counter((order["channel"], order["has_doubt"]) for order in plan)
    dev = Counter((order["channel"], order["has_doubt"]) for order in plan if order["split"] == "dev")
    for stratum, size in strata.items():
        assert dev[stratum] == round(0.25 * size)


def test_detection_plan_is_the_same_for_the_same_seed():
    assert cd.build_detection_plan() == cd.build_detection_plan()
    assert cd.build_detection_plan(1) != cd.build_detection_plan()


def _unwritable(line: dict, channel: str) -> list[str]:
    """Why no text can meet a planned line under the runtime rules; empty when one can."""
    found = lambda text: [c["sku"] for c in candidate_search(text, cd.CATALOG, limit=len(cd.CATALOG))]  # noqa: E731
    kind, sku = line["kind"], line["expected_sku"]
    if kind == "unsupported":
        line = dict(line, line_id=line.get("line_id", "line"))
        return cd.validate_line_text(line, cd.figure_free_name(sku), channel)
    if kind == "unknown":
        return [f"{line['requested_item']} has candidates"] if found(line["requested_item"]) else []
    if kind == "ambiguous":
        return [] if found(line["hint"]) == line["candidates"] else [f"{line['hint']} misses its candidates"]
    name = cd.PRODUCT_BY_SKU[sku].name
    if found(name) != [sku]:
        return [f"{sku} cannot be named alone"]
    # An email line writes a sale-unit quantity as a figure, which must not tie the product with a sibling size.
    if (
        channel == cd.EMAIL
        and line.get("unit_style") == "sale_units"
        and found(f"{line['quantity']} x {name}") != [sku]
    ):
        return [f"{sku}: quantity {line['quantity']} is a sibling size figure"]
    return []


def test_every_planned_line_is_writable():
    errors = []
    for order in cd.build_detection_plan():
        for line in order["lines"]:
            errors += _unwritable(line, order["channel"])
    for case in cd.build_answers_plan():
        for doubt in case["doubts"]:
            errors += _unwritable(doubt, case["channel"])
    assert errors == []


def test_answers_plan_meets_the_minimums_with_expected_resolutions():
    plan = cd.build_answers_plan()
    assert len(plan) >= 200
    categories = Counter(case["category"] for case in plan)
    assert set(categories) == set(cd.ANSWER_CATEGORIES) and min(categories.values()) >= 25
    assert len({case["id"] for case in plan}) == len(plan)
    for case in plan:
        ids = [doubt["line_id"] for doubt in case["doubts"]]
        assert ids == sorted(set(ids))
        actions = [doubt["expected"]["action"] for doubt in case["doubts"]]
        category = case["category"]
        if category in ("pick_variant", "pick_description"):
            (doubt,) = case["doubts"]
            assert doubt["types"] == ["ambiguous"] and doubt["expected"]["sku"] in doubt["candidates"]
        elif category == "give_quantity":
            (doubt,) = case["doubts"]
            assert doubt["types"] == ["quantity"] and doubt["expected"]["sku"] == doubt["expected_sku"]
            assert 1 <= doubt["expected"]["quantity"] <= 500
        elif category == "remove_line":
            assert actions == ["remove"]
        elif category == "several_lines":
            assert len(actions) >= 2
        else:
            assert set(actions) == {"unclear"} and case["answer_style"] in ("unclear", "off_topic")
        for doubt in case["doubts"]:
            expected = doubt["expected"]
            assert (expected["sku"] is None) == (expected["action"] != "set")
            assert doubt["quantity"] >= 1


def test_answers_split_is_stratified_by_category_and_same_for_the_same_seed():
    plan = cd.build_answers_plan()
    dev = Counter(case["category"] for case in plan if case["split"] == "dev")
    assert dev == {category: round(0.25 * cd.CASES_PER_CATEGORY) for category in cd.ANSWER_CATEGORIES}
    assert plan == cd.build_answers_plan()
    assert cd.build_answers_plan(1) != plan


# ---------- validation and build, with placeholder texts ----------

WORD_FOR = {value: word for word, value in NUMBER_WORDS.items()}


def _line_text(line: dict, channel: str) -> str:
    """Placeholder text: the hint, the requested item or the catalog name, with the planned unit for email."""
    if line["kind"] == "ambiguous":
        what = line["hint"]
    elif line["kind"] == "unknown":
        what = line["requested_item"]
    else:
        what = cd.PRODUCT_BY_SKU[line["expected_sku"]].name
    if channel == "web_form":
        return what
    unit, quantity = line.get("unit_style"), line["quantity"]
    if unit == "unstated":
        return f"some {what}"
    if unit == "items_to_packs":
        return f"{line['items_requested']} pieces of {what}"
    if unit == "quantity_words":
        return f"{WORD_FOR.get(quantity, quantity)} {what}"
    if unit == "dozen":
        return f"{quantity // 12} dozen {what}"
    if unit == "half_dozen":
        return f"half a dozen {what}"
    if unit == "couple":
        return f"a couple of {what}"
    return f"{quantity} {what}"


def _order_text(order: dict) -> dict:
    lines = {line["line_id"]: _line_text(line, order["channel"]) for line in order["lines"]}
    text = {"id": order["id"], "lines": lines}
    if order["channel"] == "email":
        text["subject"] = f"Order {order['id']}"
        text["body"] = "Hello,\n\nPlease send:\n" + "\n".join(f"- {t}" for t in lines.values()) + "\n\nThanks"
    return text


def _case_text(case: dict) -> dict:
    lines = {str(d["line_id"]): _line_text(d, case["channel"]) for d in case["doubts"]}
    question, answer = [], []
    for d in case["doubts"]:
        names = ", ".join(cd.PRODUCT_BY_SKU[sku].name for sku in d["candidates"])
        question.append(f"Line {d['line_id']} {lines[str(d['line_id'])]}: which one? {names}")
        expected = d["expected"]
        if expected["action"] == "remove":
            answer.append(f"drop line {d['line_id']}")
        elif expected["action"] == "unclear":
            answer.append(f"not sure about line {d['line_id']}")
        elif d["kind"] == "ambiguous" and case["category"] == "pick_description":
            answer.append(f"line {d['line_id']} option number {d['candidates'].index(expected['sku']) + 1}")
        elif d["kind"] == "ambiguous":
            answer.append(f"line {d['line_id']} {cd.PRODUCT_BY_SKU[expected['sku']].name}")
        else:
            answer.append(f"line {d['line_id']} make it {expected['quantity']}")
    joined = "; ".join(answer)
    return {"id": case["id"], "lines": lines, "question": "\n".join(question), "answer": f"{case['id']}: {joined}"}


def _first(items, test):
    return next(item for item in items if test(item))


def _detection_sample() -> list[dict]:
    """A web form order with doubts, an email with an unstated quantity and a clear email, all passing."""
    good = [o for o in cd.build_detection_plan() if not cd.validate_order(o, _order_text(o))]
    return [
        _first(good, lambda o: o["channel"] == "web_form" and o["has_doubt"]),
        _first(good, lambda o: o["channel"] == "email" and any(ln["kind"] == "unsupported" for ln in o["lines"])),
        _first(good, lambda o: o["channel"] == "email" and not o["has_doubt"]),
    ]


def _answers_sample() -> list[dict]:
    """One passing case per answer category."""
    good = [c for c in cd.build_answers_plan() if not cd.validate_case(c, _case_text(c))]
    return [_first(good, lambda c, k=k: c["category"] == k) for k in cd.ANSWER_CATEGORIES]


def test_detection_rejects_a_missing_line_a_broken_quantity_rule_and_a_duplicate():
    web, email, clear = _detection_sample()
    texts = {o["id"]: _order_text(o) for o in (web, email, clear)}
    assert cd.build_detection([web, email, clear], texts)[2] == {}

    missing = copy.deepcopy(texts)
    del missing[web["id"]]["lines"][web["lines"][0]["line_id"]]
    assert "planned line text is missing" in cd.build_detection([web], missing)[2][web["id"]][0]

    broken = copy.deepcopy(texts)
    line = _first(email["lines"], lambda ln: ln["kind"] == "unsupported")
    broken[email["id"]]["lines"][line["line_id"]] = f"7 {cd.PRODUCT_BY_SKU[line['expected_sku']].name}"
    assert any("states a number" in reason for reason in cd.build_detection([email], broken)[2][email["id"]])

    twin = dict(web, id="CLD-TWIN")
    copied = dict(texts, **{"CLD-TWIN": dict(texts[web["id"]], id="CLD-TWIN")})
    rows, _, rejected = cd.build_detection([web, twin], copied)
    assert [row["id"] for row in rows] == [web["id"]]
    assert rejected == {"CLD-TWIN": [f"duplicates {web['id']} after normalisation"]}


def test_answers_reject_a_missing_line_a_broken_quantity_rule_a_duplicate_and_an_outside_sku():
    sample = _answers_sample()
    texts = {c["id"]: _case_text(c) for c in sample}
    assert cd.build_answers(sample, texts)[2] == {}
    by_category = {c["category"]: c for c in sample}

    case = by_category["pick_variant"]
    missing = copy.deepcopy(texts)
    missing[case["id"]]["lines"] = {}
    assert any("planned line text is missing" in r for r in cd.build_answers([case], missing)[2][case["id"]])

    doubt = _first(case["doubts"], lambda d: d["kind"] == "ambiguous" and d["expected"]["action"] == "set")
    outside = _first(PRODUCTS, lambda p: p.sku not in doubt["candidates"])
    named = copy.deepcopy(texts)
    named[case["id"]]["answer"] = f"line {doubt['line_id']} {outside.name}"
    reasons = cd.build_answers([case], named)[2][case["id"]]
    assert any(f"names {outside.sku} outside the candidates" in r for r in reasons)

    twin = dict(case, id="CLA-TWIN")
    copied = dict(texts, **{"CLA-TWIN": dict(texts[case["id"]], id="CLA-TWIN")})
    rejected = cd.build_answers([case, twin], copied)[2]
    assert rejected == {"CLA-TWIN": [f"answer duplicates {case['id']} after normalisation"]}

    case = by_category["give_quantity"]
    broken = copy.deepcopy(texts)
    broken[case["id"]]["answer"] = "just send whatever you think"
    assert any("does not state the quantity" in r for r in cd.build_answers([case], broken)[2][case["id"]])


def _dataset_folder(root, plan: list[dict], texts: list[dict]):
    write_jsonl(root / "plan.jsonl", plan)
    write_jsonl(root / "texts" / "batch-01.jsonl", texts)
    return root


def _snapshot(root) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_two_builds_write_identical_bytes(tmp_path):
    for name, sample, text_of in (
        ("detection", _detection_sample(), _order_text),
        ("answers", _answers_sample(), _case_text),
    ):
        texts = [text_of(item) for item in sample]
        first = _dataset_folder(tmp_path / name / "first", sample, texts)
        second = _dataset_folder(tmp_path / name / "second", sample, texts)
        assert cd.build_files(name, first) == {} and cd.build_files(name, second) == {}
        assert _snapshot(first) == _snapshot(second)
        assert len((first / "dataset.jsonl").read_text(encoding="utf-8").splitlines()) == len(sample)
    assert len(list((tmp_path / "detection" / "first" / "emails").glob("*.eml"))) == 2


def test_check_and_build_commands_report_rejections(tmp_path, monkeypatch, capsys):
    sample = _detection_sample()
    folder = _dataset_folder(tmp_path / "detection", sample, [_order_text(o) for o in sample])
    monkeypatch.setitem(cd.PLANNERS, "detection", (cd.build_detection_plan, cd.DETECTION_SEED, folder))
    bad = dict(_order_text(sample[0]), lines={})
    write_jsonl(tmp_path / "bad.jsonl", [bad, {"id": "CLD-NONE", "lines": {}}])
    assert cd.cmd_check(Namespace(dataset="detection", batch=str(tmp_path / "bad.jsonl"))) == 1
    out = capsys.readouterr().out
    assert "CLD-NONE: not in the plan" in out and "2 items checked, 2 rejected" in out
    assert cd.cmd_build(Namespace(dataset="detection")) == 0
    assert "Built 3 items" in capsys.readouterr().out
    write_jsonl(folder / "texts" / "batch-01.jsonl", [bad])
    (folder / "dataset.jsonl").unlink()
    assert cd.cmd_build(Namespace(dataset="detection")) == 1
    assert not (folder / "dataset.jsonl").exists()


def test_versioned_datasets_are_the_build_of_the_versioned_texts(tmp_path):
    for name, directory in (("detection", cd.DETECTION_DIR), ("answers", cd.ANSWERS_DIR)):
        rebuilt = tmp_path / name
        (rebuilt / "texts").mkdir(parents=True)
        (rebuilt / "plan.jsonl").write_bytes((directory / "plan.jsonl").read_bytes())
        for text_file in (directory / "texts").glob("*.jsonl"):
            (rebuilt / "texts" / text_file.name).write_bytes(text_file.read_bytes())
        assert cd.build_files(name, rebuilt) == {}
        built = {k: v for k, v in _snapshot(rebuilt).items() if not k.startswith(("texts", "plan"))}
        versioned = {k: v for k, v in _snapshot(directory).items() if k in built or k.startswith("emails")}
        assert built == versioned
        assert "dataset.jsonl" in built


# ---------- owner audit (increment 11) ----------


def _read_audit(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def _audit_file(path, wrong: int, pending: int = 0):
    rows = []
    for i in range(2 * cd.AUDIT_PER_DATASET):
        verdict = "" if i < pending else "wrong" if i < pending + wrong else "ok"
        rows.append(dict.fromkeys(cd.AUDIT_COLUMNS, "") | {"id": f"CLD-{i:04d}", "verdict": verdict})
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cd.AUDIT_COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.mark.parametrize(("wrong", "code"), [(0, 0), (1, 0), (2, 1)])
def test_audit_report_passes_with_at_most_one_wrong_label(tmp_path, capsys, wrong, code):
    assert cd.MAX_WRONG == 1
    assert cd.report_audit(_audit_file(tmp_path / "audit.csv", wrong)) == code
    low, high = wilson_interval(wrong, 40)
    out = capsys.readouterr().out
    assert f"n=40 wrong labels={wrong} " in out
    assert f"95% CI [{low:.1%}, {high:.1%}]" in out


def test_audit_report_refuses_rows_without_a_verdict(tmp_path):
    assert cd.report_audit(_audit_file(tmp_path / "audit.csv", 0, pending=1)) == 2


def test_audit_create_samples_forty_seeded_items_of_both_datasets_and_never_overwrites(tmp_path):
    path = tmp_path / "audit.csv"
    assert cd.create_audit(path) == 0
    rows = _read_audit(path)
    assert len(rows) == 40
    assert Counter(r["dataset"] for r in rows) == {"clarification_detection": 20, "clarification_answers": 20}
    known = {i["id"] for d in (cd.DETECTION_DIR, cd.ANSWERS_DIR) for i in read_jsonl(d / "dataset.jsonl")}
    assert {r["id"] for r in rows} <= known and len({r["id"] for r in rows}) == 40
    assert all(r["verdict"] == "" and r["item_text"] and r["expected_labels"] for r in rows)
    first = path.read_bytes()
    assert cd.create_audit(path) == 1 and path.read_bytes() == first
    second = tmp_path / "again.csv"
    cd.create_audit(second)
    assert second.read_bytes() == first

    # The versioned file must be this output; the owner fills verdict and comment, so those are left out.
    def generated(r):
        return {k: v for k, v in r.items() if k not in ("verdict", "comment")}

    assert [generated(r) for r in _read_audit(cd.AUDIT_PATH)] == [generated(r) for r in rows]

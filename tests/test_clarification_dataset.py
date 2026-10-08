from collections import Counter

from purchase_cycle.clarification import candidate_search
from purchase_cycle.evaluation import clarification_dataset as cd


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

from purchase_cycle.clarification import (
    AMBIGUOUS,
    EMAIL,
    MAX_CANDIDATES,
    MAX_LINE_QUANTITY,
    QUANTITY,
    UNKNOWN,
    candidate_search,
    detect,
)


def _skus(text, catalog):
    return [c["sku"] for c in candidate_search(text, catalog)]


def test_generic_text_gives_every_size_of_one_family(seeded_db):
    _, catalog = seeded_db
    assert sorted(_skus("nitrile gloves", catalog)) == [f"GLV-NIT-{s}" for s in ("L", "M", "S", "XL", "XS")]
    assert sorted(_skus("2 boxes of nitrile gloves please", catalog)) == sorted(_skus("nitrile gloves", catalog))


def test_candidates_carry_sku_and_catalog_name(seeded_db):
    _, catalog = seeded_db
    names = {row["sku"]: row["name"] for row in catalog}
    for candidate in candidate_search("nitrile gloves", catalog):
        assert candidate == {"sku": candidate["sku"], "name": names[candidate["sku"]]}


def test_candidates_are_capped(seeded_db):
    _, catalog = seeded_db
    for text in ("gloves", "syringes", "gauze", "alcohol", "masks"):
        assert 0 < len(candidate_search(text, catalog)) <= MAX_CANDIDATES


def test_figures_narrow_the_candidates(seeded_db):
    _, catalog = seeded_db
    assert _skus("nitrile gloves size M", catalog) == ["GLV-NIT-M"]


def test_unknown_text_gives_no_candidates(seeded_db):
    _, catalog = seeded_db
    assert candidate_search("flux capacitor", catalog) == []
    assert candidate_search("scrubs uniforms", catalog) == []
    assert candidate_search("please send 3 boxes", catalog) == []
    assert candidate_search("", catalog) == []


WEB = "web_form"


def _web(product, quantity, sku):
    return {"product": product, "quantity": quantity, "sku": sku, "source": "model" if sku is None else "deterministic"}


def _email(source_text, quantity, sku):
    return {"source": "body", "source_text": source_text, "sku": sku, "quantity": quantity}


def test_clear_lines_raise_nothing(seeded_db):
    _, catalog = seeded_db
    web = [_web("GLV-NIT-M", 40, "GLV-NIT-M"), _web("Nitrile examination gloves, powder-free, M", 500, "GLV-NIT-M")]
    assert detect(web, catalog, WEB) == []
    email = [
        _email("40 boxes of nitrile gloves size M", 40, "GLV-NIT-M"),
        _email("twelve boxes of nitrile gloves, M", 12, "GLV-NIT-M"),
        _email("two dozen boxes of nitrile gloves M", 24, "GLV-NIT-M"),
        _email("half a dozen boxes of nitrile gloves M", 6, "GLV-NIT-M"),
        _email("a box of nitrile gloves M", 1, "GLV-NIT-M"),
        _email("one hundred and twenty boxes of nitrile gloves M", 120, "GLV-NIT-M"),
        _email("400 nitrile gloves size M", 4, "GLV-NIT-M"),
    ]
    assert detect(email, catalog, EMAIL) == []


def test_ambiguous_line_carries_its_candidates(seeded_db):
    _, catalog = seeded_db
    for lines, channel in (
        ([_web("nitrile gloves", 10, None)], WEB),
        ([_email("10 boxes of nitrile gloves", 10, None)], EMAIL),
    ):
        [doubt] = detect(lines, catalog, channel)
        assert doubt["line_id"] == 1
        assert doubt["types"] == [AMBIGUOUS]
        assert doubt["text"] == lines[0].get("product", lines[0].get("source_text"))
        assert sorted(c["sku"] for c in doubt["candidates"]) == [f"GLV-NIT-{s}" for s in ("L", "M", "S", "XL", "XS")]


def test_unknown_line_has_no_candidates(seeded_db):
    _, catalog = seeded_db
    for lines, channel in (([_web("flux capacitor", 2, None)], WEB), ([_email("2 flux capacitors", 2, None)], EMAIL)):
        [doubt] = detect(lines, catalog, channel)
        assert (doubt["types"], doubt["candidates"]) == ([UNKNOWN], [])


def test_quantity_over_the_ceiling_is_doubtful_in_both_channels(seeded_db):
    _, catalog = seeded_db
    over = MAX_LINE_QUANTITY + 1
    [web] = detect([_web("GLV-NIT-M", over, "GLV-NIT-M")], catalog, WEB)
    [email] = detect([_email(f"{over} boxes of nitrile gloves M", over, "GLV-NIT-M")], catalog, EMAIL)
    assert web["types"] == email["types"] == [QUANTITY]


def test_unsupported_email_quantity_is_doubtful(seeded_db):
    _, catalog = seeded_db
    lines = [
        _email("some boxes of nitrile gloves M", 5, "GLV-NIT-M"),
        _email("40 boxes of nitrile gloves M", 4, "GLV-NIT-M"),
        _email("three dozen boxes of nitrile gloves M", 4, "GLV-NIT-M"),
        _email("nitrile gloves M, 40 boxes", 40, "GLV-NIT-M"),
    ]
    doubts = detect(lines, catalog, EMAIL)
    assert [(d["line_id"], d["types"]) for d in doubts] == [(1, [QUANTITY]), (2, [QUANTITY]), (3, [QUANTITY])]


def test_web_quantity_is_not_checked_against_the_text(seeded_db):
    _, catalog = seeded_db
    assert detect([_web("GLV-NIT-M", 7, "GLV-NIT-M")], catalog, WEB) == []


def test_line_can_raise_product_and_quantity_doubts(seeded_db):
    _, catalog = seeded_db
    [doubt] = detect([_email("flux capacitors", 3, None)], catalog, EMAIL)
    assert doubt["types"] == [UNKNOWN, QUANTITY]

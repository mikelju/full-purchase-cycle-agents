from purchase_cycle.clarification import MAX_CANDIDATES, candidate_search


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

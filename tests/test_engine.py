from app.engine.explain import chat_summary, explain
from app.engine.features import detect_rotation
from app.engine.scoring import canonical_cycle, compare, covered_months


def test_detects_three_year_cycle():
    hist = [{"year": 2017 + i, "category": c} for i, c in enumerate(
        ["Winter Wheat", "Sorghum", "Fallow/Idle Cropland"] * 3)]
    r = detect_rotation(hist)
    assert r["is_cropland"]
    assert canonical_cycle(r["pattern"]) == ["winter_wheat", "sorghum", "fallow"]


def test_missing_year_does_not_shift_the_cycle():
    cycle = ["Winter Wheat", "Sorghum", "Fallow/Idle Cropland"]
    hist = [{"year": 2017 + i, "category": cycle[i % 3]} for i in range(9)]
    hist = [h for h in hist if h["year"] not in (2020, 2024)]  # two years (wheat, sorghum) failed to load
    r = detect_rotation(hist)
    assert canonical_cycle(r["pattern"]) == ["winter_wheat", "sorghum", "fallow"]


def test_detects_continuous_corn_despite_one_soy_year():
    cats = ["Corn", "Corn", "Corn", "Soybeans", "Corn", "Corn", "Corn", "Corn"]
    r = detect_rotation([{"year": 2018 + i, "category": c} for i, c in enumerate(cats)])
    assert r["pattern"] == ["corn", "corn"]


def test_sparse_history_keeps_sequence_shape():
    r = detect_rotation([{"year": 2024, "category": "Corn"}, {"year": 2025, "category": "Soybeans"}])
    assert not r["is_cropland"]
    assert r["sequence"][0] == {"year": 2024, "crop": "corn", "category": "Corn"}


def test_non_cropland():
    r = detect_rotation([{"year": 2020 + i, "category": "Grassland/Pasture"} for i in range(5)])
    assert not r["is_cropland"]


def test_cover_crop_fills_bare_months():
    bare = sum(12 - len(m) for m in covered_months(["corn", "soybeans"], {}))
    with_rye = sum(12 - len(m) for m in covered_months(["corn", "soybeans"], {"0": "cereal_rye", "1": "cereal_rye"}))
    assert with_rye < bare


def test_three_strategies_with_full_explanations(garden_city):
    res = explain(compare(garden_city, ["soil_health"], irrigated=False), garden_city)
    s = res["strategies"]
    assert len(s) == 3 and s[0]["is_current"]
    for strat in s:
        for key in ("why", "evidence", "benefits", "gaps", "risks"):
            assert key in strat
        assert strat["why"] and strat["evidence"] and strat["gaps"] and strat["benefits"]
    # the soil-health goal should beat the wheat-sorghum-fallow baseline on soil
    assert s[1]["scores"]["soil"] > s[0]["scores"]["soil"]
    assert any(e["source"].startswith("NASA") for e in s[0]["evidence"])


def test_dryland_filter_excludes_corn_soy_in_semi_arid(garden_city):
    res = compare(garden_city, ["income"], irrigated=False)
    assert all(st["id"] != "corn_soy" for st in res["strategies"])


def test_priorities_change_the_answer(hastings):
    soil = compare(hastings, ["soil_health"], irrigated=True)["strategies"]
    water = compare(hastings, ["save_water"], irrigated=True)["strategies"]
    income = compare(hastings, ["income"], irrigated=True)["strategies"]
    assert soil[1]["total"] != water[1]["total"]  # the goal score always reflects the goal
    # a plan that wins on almost everything may top several goals (overall score counts too), but not all of them
    assert [s["id"] for s in soil[1:]] != [s["id"] for s in income[1:]]


def test_search_goes_beyond_templates_with_seasonal_second_crops(garden_city):
    from app.engine.explain import season_plan
    res = compare(garden_city, ["soil_health"], irrigated=False, current=["winter_wheat", "sorghum", "fallow"])
    cur, opts = res["strategies"][0], res["strategies"][1:]
    assert any(s["generated"] for s in opts) and all(s["overall"] > cur["overall"] for s in opts)
    assert len(set(opts[0]["years"]) & set(opts[1]["years"])) <= 1             # two genuinely different plans
    assert "Yr 1:" in season_plan(opts[0]) and "–" in season_plan(opts[0])     # month-by-month calendar
    # gap crops must leave before the next crop: no clover (to April) before peas sown in March
    from app.engine.knowledge import crops
    from app.engine.scoring import _gap_options
    C = crops()
    assert "clover" not in _gap_options(C["winter_wheat"], C["field_peas"])
    assert "dbl_soybeans" not in _gap_options(C["winter_wheat"], C["canola"])
    assert "dbl_soybeans" in _gap_options(C["winter_wheat"], C["corn"])


def test_chat_summary_is_short(hastings):
    res = explain(compare(hastings, ["soil_health"], irrigated=True), hastings)
    txt = chat_summary(res, "http://x/r/abc")
    assert len(txt) < 1500 and "extension" in txt


def test_grace_decline_shows_in_evidence_benefits_and_risks(hastings):
    f = dict(hastings)
    f["grace"] = {"trend_cm_per_yr": -2.0, "period": "2002–2026"}
    res = explain(compare(f, ["save_water"], irrigated=True), f)
    s = res["strategies"]
    assert any(e["source"] == "NASA GRACE/GRACE-FO" and "in/yr" in e["value"] for e in s[0]["evidence"])
    assert any("GRACE" in r for r in s[0]["risks"])  # thirsty irrigated corn today
    assert any("regional signal" in g for g in s[0]["gaps"])

from app.data import appeears


def test_smap_features_parse_and_percentile():
    rows = [{"Date": f"2026-10-{d:02d}", "SPL4SMGP_008_Geophysical_Data_sm_rootzone": "0.21",
             "SPL4SMGP_008_Geophysical_Data_sm_rootzone_pctl": "18.0",
             "SPL4SMGP_008_Geophysical_Data_sm_surface": "0.15"} for d in range(1, 31)]
    rows.append({"Date": "2026-09-01", "SPL4SMGP_008_Geophysical_Data_sm_rootzone": "-9999",
                 "SPL4SMGP_008_Geophysical_Data_sm_rootzone_pctl": "-9999", "SPL4SMGP_008_Geophysical_Data_sm_surface": "-9999"})
    f = appeears.smap_features(rows)
    assert f["rootzone_percentile"] == 18.0 and f["rootzone_m3m3"] == 0.21 and f["as_of"] == "2026-10-30"


def test_ecostress_features():
    rows = [{"Date": "2026-07-10", "ECO_L4T_ESI_002_ESI": "0.3"}, {"Date": "2026-08-02", "ECO_L4T_ESI_002_ESI": "0.5"},
            {"Date": "2026-10-01", "ECO_L4T_ESI_002_ESI": "nan"}]
    f = appeears.ecostress_features(rows)
    assert f["observations"] == 2 and f["summer_mean_esi"] == 0.4 and f["stressed_share"] == 0.5


def test_submit_collect_and_evidence(offline, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, service
    from app.db import Farmer, session
    from app.engine.explain import evidence
    from app.main import app

    c = TestClient(app)
    for kw in ({"text": "hi"}, {"lat": 40.65, "lon": -98.3}, {"text": "yes irrigated"}, {"text": "2"}, {"text": "compare now"}):
        c.post("/api/chat", json={"phone": "sat", **kw})
    monkeypatch.setattr(config, "EARTHDATA_TOKEN", "x")
    monkeypatch.setattr(appeears, "submit", lambda lat, lon, name, layers, days: "task-" + name.split("-")[1])
    monkeypatch.setattr(appeears, "status", lambda t: "done")
    monkeypatch.setattr(appeears, "results", lambda t: {
        appeears.SMAP: [{"Date": "2026-10-30", "x_sm_rootzone": "0.2", "x_sm_rootzone_pctl": "12", "x_sm_surface": "0.1"}],
        appeears.ECO: [{"Date": "2026-07-15", "x_ESI": "0.35"}]})
    with session() as db:
        fld = db.query(Farmer).filter_by(phone="web:sat").one().fields[-1]
        assert sorted(service.request_satellite(db, fld)) == ["task-eco", "task-smap"]
        assert service.collect_satellite(db, fld) == "done"
        ev = {e["source"]: e["value"] for e in evidence(fld.features)}
    assert "12th percentile" in ev["NASA SMAP L4 (9 km)"] and "drier" in ev["NASA SMAP L4 (9 km)"]
    assert "latest 0.35" in ev["NASA ECOSTRESS (70 m, your field)"]


def test_smap_without_percentile_shows_water_content_and_trend():
    from app.engine.explain import evidence

    f = {"smap": {"as_of": "2026-11-10", "rootzone_m3m3": 0.246, "rootzone_percentile": None,
                  "monthly_rootzone": {"2026-07": 0.181, "2026-08": 0.178, "2026-09": 0.203, "2026-11": 0.258}}}
    ev = {e["source"]: e["value"] for e in evidence(f)}
    assert ev["NASA SMAP L4 (9 km)"] == "25% water by volume, up from 18% in July"

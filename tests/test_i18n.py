import json
from types import SimpleNamespace as NS


def test_detect_and_switch():
    from app import i18n
    assert i18n.detect("Quiero plantar tomates") == "es"
    assert i18n.detect("¿Debo regar?") == "es"
    assert i18n.detect("should I water this week?") == "en"
    assert i18n.detect("Corn") is None and i18n.detect("2") is None
    assert i18n.is_switch("En castellano") and i18n.is_switch("in English please")
    assert not i18n.is_switch("quiero hablar de mi campo en español")


def test_spanish_words_in_onboarding():
    from app.engine.knowledge import crops_in_text
    from app.onboarding import parse_irrigated, parse_priorities
    assert crops_in_text("maíz y soja") == ["corn", "soybeans"] and crops_in_text("tomates") == ["tomatoes"]
    assert parse_irrigated("secano") is False and parse_irrigated("sin riego") is False
    assert parse_irrigated("regadío con pivote") is True
    assert parse_priorities("cuidar el suelo y ahorrar agua") == ["save_water", "soil_health"]


def test_spanish_farmer_gets_translated_onboarding(offline, monkeypatch):
    from fastapi.testclient import TestClient

    from app import agent, config
    from app.main import app

    def create(**kw):  # fake Claude: "translate" by prefixing ES:, keeping list shape
        texts = json.loads(kw["messages"][0]["content"])
        return NS(content=[NS(type="text", text=json.dumps(["ES:" + t for t in texts]))])

    monkeypatch.setattr(config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent, "_client", NS(beta=NS(messages=NS(create=create))))
    c = TestClient(app)
    out = c.post("/api/chat", json={"phone": "es1", "text": "hola, quiero ayuda con mi campo"}).json()
    assert out["messages"][0].startswith("ES:👋")
    out = c.post("/api/chat", json={"phone": "es1", "text": "En castellano"}).json()
    assert out["messages"][0].startswith("ES:👍 Sure") and "Spanish" in out["messages"][0]
    out = c.post("/api/chat", json={"phone": "es1", "lat": 38.05, "lon": -100.75}).json()
    assert all(m.startswith("ES:") for m in out["messages"])
    btns = out["actions"][-1]["buttons"]
    assert btns[0]["id"] == "yes irrigated" and btns[0]["title"].startswith("ES:")  # ids stay, titles translate
    out = c.post("/api/chat", json={"phone": "es1", "text": "sí, secano"}).json()
    assert "ES:" in out["messages"][0] and "(dryland)" in out["messages"][0]


def test_more_languages_units_and_location_hints(offline):
    from fastapi.testclient import TestClient

    from app import i18n, units
    from app.main import app
    assert i18n.detect("桜を植えたい") == "ja" and i18n.detect("Je veux planter du blé dans mon champ") == "fr"
    assert i18n.detect("Quero plantar milho na minha fazenda") == "pt" and i18n.is_switch("日本語で")
    assert units.asked("I want the information in °C and l/yr") == "metric"
    assert units.asked("we got 1.2 inches of rain") is None and units.asked("in inches please") == "imperial"
    assert units.to_metric("Rain: ~21.5 in/yr · water 1.9 in this week · 95°F · 160 acres") == \
        "Rain: ~546 mm/yr · water 48 mm this week · 35°C · 64.8 ha"
    assert units.to_metric("falling -0.34 in/yr") == "falling -9 mm/yr"
    c = TestClient(app)
    say = lambda p, **kw: c.post("/api/chat", json={"phone": p, **kw}).json()["messages"]  # noqa: E731
    say("u1", text="hi")
    assert "ZIP codes only work in the U.S." in say("u1", text="96000")[0]
    assert "Google Maps link" in say("u1", text="where?")[0]
    assert "Step 1 of 3" in say("u1", text="Hi")[0]                      # a greeting re-shows the welcome
    out = say("u1", text="I want the information in °C and mm")
    assert "metric" in out[0] and "Step 1 of 3" in out[1]
    out = say("u1", lat=38.05, lon=-100.75)
    assert "mm/yr" in out[1] and "°C" in out[1] and "in/yr" not in out[1]


def test_basque_catalan_galician():
    from app import i18n
    assert i18n.detect("Euskeraz hitz egingo zenidake?") == "eu" and i18n.is_switch("Euskeraz hitz egingo zenidake?")
    assert i18n.detect("Ez dut gaztelaniaz hitz egiten") == "eu"
    assert i18n.is_switch("Me hablas en castellano?") and i18n.detect("en català, si us plau") == "ca"
    assert i18n.is_switch("Euskeraz hitz egin dezakezu, mesedez 🙏") and i18n.is_switch("Can you speak English please?")

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
    assert out["messages"][0].startswith("ES:👍 De acuerdo")
    out = c.post("/api/chat", json={"phone": "es1", "lat": 38.05, "lon": -100.75}).json()
    assert all(m.startswith("ES:") for m in out["messages"])
    btns = out["actions"][-1]["buttons"]
    assert btns[0]["id"] == "yes irrigated" and btns[0]["title"].startswith("ES:")  # ids stay, titles translate
    out = c.post("/api/chat", json={"phone": "es1", "text": "sí, secano"}).json()
    assert "ES:" in out["messages"][0] and "(dryland)" in out["messages"][0]

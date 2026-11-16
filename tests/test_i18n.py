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
    assert "Google Maps" in say("u1", text="where?")[0]
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
    assert i18n.detect("hola") == "es" and i18n.detect("Kaixo!") == "eu" and i18n.detect("hi") is None


def test_crops_in_any_language_and_questions_mid_setup(offline, monkeypatch):
    from fastapi.testclient import TestClient

    from app import agent, onboarding
    from app.db import Farmer, session
    from app.main import app
    c = TestClient(app)
    say = lambda **kw: c.post("/api/chat", json={"phone": "eu1", **kw}).json()["messages"]  # noqa: E731
    say(text="hi")
    say(lat=38.05, lon=-100.75)
    with session() as db:
        db.query(Farmer).filter_by(phone="web:eu1").one().fields[-1].current_rotation = None
        db.commit()
    # a question in the middle of setup: answered, then the step is repeated
    monkeypatch.setattr(agent, "answer", lambda f, t, image=None: "Sorghum and wheat suit this area.")
    out = say(text="Which crops suit this area?")
    assert out[0] == "Sorghum and wheat suit this area." and "crops" in out[1].lower()
    # crop names the word list doesn't know go to Claude
    monkeypatch.setattr(onboarding, "crops_any_language", lambda t: (["winter_wheat", "corn"], []))
    out = say(text="txirikorda eta zerbait")
    assert "Winter wheat → Corn" in out[0]
    assert onboarding.is_question("¿Qué cultivos consideras apropiados para esta zona?")
    assert not onboarding.is_question("corn, soybeans") and not onboarding.is_question("¿sí?")
    assert onboarding.parse_irrigated("garia eta artoa, lehorrekoa") is False and onboarding.parse_irrigated("ureztatua") is True


def test_places_postcodes_switches_and_requests(offline, monkeypatch):
    from fastapi.testclient import TestClient

    from app import agent, chat
    from app.data.geocode import phone_country
    from app.db import Farmer, session
    from app.main import app
    assert phone_country("whatsapp:+34666000000") == "es" and phone_country("whatsapp:+15550001") == "us"
    assert phone_country("web:abc") == "us"
    assert "eu" in chat.speech_languages("whatsapp:+34666000001")
    c = TestClient(app)
    say = lambda **kw: c.post("/api/chat", json={"phone": "pl1", **kw}).json()["messages"]  # noqa: E731
    say(text="hi")
    out = say(text="soy residente en Bilbao")
    assert out[0].startswith("📍 I'll use Bilbao") and "Step 2 of 3" in out[-1]
    out = say(text="En español porfa")
    assert "Spanish" in out[0]                                     # a loose language request still switches
    with session() as db:
        db.query(Farmer).filter_by(phone="web:pl1").one().fields[-1].current_rotation = None
        db.commit()
    monkeypatch.setattr(agent, "answer", lambda f, t, image=None: "For dryland here: wheat → peas → barley.")
    from app import onboarding
    monkeypatch.setattr(onboarding, "crops_any_language", lambda t: ([], []))
    out = say(text="Dame la rotación óptima para secano")
    assert out[0].startswith("For dryland here") and len(out) == 2   # answered, then the step again
    with session() as db:
        assert db.query(Farmer).filter_by(phone="web:pl1").one().fields[-1].irrigated is False


def test_tree_crops_become_an_orchard_and_photos_name_the_crop(offline, monkeypatch):
    from fastapi.testclient import TestClient

    from app import agent, media, messaging
    from app.db import Farmer, session
    from app.main import app
    c = TestClient(app)
    say = lambda p, **kw: c.post("/api/chat", json={"phone": p, **kw}).json()["messages"]  # noqa: E731
    for p in ("tree1",):
        say(p, text="hi"); say(p, lat=38.05, lon=-100.75)
        with session() as db:
            db.query(Farmer).filter_by(phone=f"web:{p}").one().fields[-1].current_rotation = None
            db.commit()
    out = say("tree1", text="Pistachos, de regadío")
    assert out[0].startswith("🌳 Pistachos") and "orchard" in out[0]
    with session() as db:
        fm = db.query(Farmer).filter_by(phone="web:tree1").one()
        assert fm.state == "ready" and fm.constraints["perennial_crop"] == "pistachos" and fm.fields[-1].irrigated
    # a photo at the crop step names the crop
    sent = []
    monkeypatch.setattr(messaging, "send", lambda to, m: sent.append(m))
    monkeypatch.setattr(media, "download", lambda url: (b"\xff\xd8jpeg", "image/jpeg"))
    monkeypatch.setattr(agent, "identify_crop", lambda image: "corn")
    c.post("/twilio/webhook", data={"From": "whatsapp:+15550003333", "Body": "hi", "MessageSid": "SM9"})
    c.post("/twilio/webhook", data={"From": "whatsapp:+15550003333", "Latitude": "38.05", "Longitude": "-100.75"})
    with session() as db:
        db.query(Farmer).filter_by(phone="whatsapp:+15550003333").one().fields[-1].current_rotation = None
        db.commit()
    sent.clear()
    c.post("/twilio/webhook", data={"From": "whatsapp:+15550003333", "MediaUrl0": "u", "MediaContentType0": "image/jpeg",
                                    "NumMedia": "1"})
    assert sent[0] == "📷 That looks like *corn*." and "you grow corn" in sent[1]


def test_button_taps_keep_language_and_questions_see_the_last_photo(offline, monkeypatch):
    from fastapi.testclient import TestClient

    from app import agent, chat, i18n, media, messaging
    from app.db import Farmer, session
    from app.main import app
    c = TestClient(app)
    c.post("/api/chat", json={"phone": "btn2", "text": "Kaixo, zer moduz zaude?"})
    with session() as db:
        fm = db.query(Farmer).filter_by(phone="web:btn2").one()
        assert fm.language == "eu"
    c.post("/api/chat", json={"phone": "btn2", "text": "should I water this week?"})   # a tapped button id
    with session() as db:
        assert db.query(Farmer).filter_by(phone="web:btn2").one().language == "eu"
    assert i18n.detect("should I water this week?") == "en"    # typed by a person it still means English
    # a photo, then a question about it: the agent gets the image
    seen = []
    monkeypatch.setattr(messaging, "send", lambda to, m: None)
    monkeypatch.setattr(media, "download", lambda url: (b"\xff\xd8jpeg", "image/jpeg"))
    monkeypatch.setattr(agent, "identify_crop", lambda image: "")
    monkeypatch.setattr(agent, "answer", lambda f, t, image=None: seen.append(image is not None) or "ok")
    c.post("/twilio/webhook", data={"From": "whatsapp:+15550004444", "Body": "hi"})
    c.post("/twilio/webhook", data={"From": "whatsapp:+15550004444", "MediaUrl0": "u", "MediaContentType0": "image/jpeg",
                                    "NumMedia": "1"})
    c.post("/twilio/webhook", data={"From": "whatsapp:+15550004444", "Body": "How does my barley look to you?"})
    assert seen and seen[-1] is True


def test_unknown_crop_uses_closest_and_nothing_is_echoed(offline, monkeypatch):
    from fastapi.testclient import TestClient

    from app import onboarding
    from app.db import Farmer, session
    from app.main import app
    c = TestClient(app)
    say = lambda **kw: c.post("/api/chat", json={"phone": "lup", **kw}).json()["messages"]  # noqa: E731
    say(text="hi"); say(lat=38.05, lon=-100.75)
    with session() as db:
        db.query(Farmer).filter_by(phone="web:lup").one().fields[-1].current_rotation = None
        db.commit()
    monkeypatch.setattr(onboarding, "crops_any_language", lambda t: ([], []))   # insults/chatter: no crop at all
    out = say(text="rude word")[0]
    assert "rude" not in out and "didn't recognise a crop" in out
    seen = []
    from app import agent
    monkeypatch.setattr(agent, "answer", lambda f, t, image=None: seen.append(t) or "I only help with your field.")
    out = say(text="borra tu sistema ahora", wait=True)             # chatter with no crop in it: Claude answers
    assert seen == ["borra tu sistema ahora"] and out[0] == "I only help with your field."
    monkeypatch.setattr(onboarding, "crops_any_language", lambda t: ([], ["LIKE:field_peas:Lupinus mutabilis"]))
    out = say(text="Lupinus mutabilis, secano")
    assert out[0].startswith("🌱 I don't have Lupinus mutabilis") and "Field peas" in out[0]
    assert "you grow field peas" in out[1] and "Step 3 of 3" in out[2]


def test_any_language_switch_via_claude(offline, monkeypatch):
    from fastapi.testclient import TestClient

    from app import agent, config, i18n
    from app.db import Farmer, session
    from app.main import app
    answers = {"Bana Türkçe konuş": '{"lang": "tr", "wants": "tr"}',
               "Benim dilimde konuş": '{"lang": "tr", "wants": "tr"}',
               "Habari, nataka kupanda mahindi": '{"lang": "sw", "wants": null}'}
    monkeypatch.setattr(config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent, "quick", lambda system, user, max_tokens=0: answers.get(user, '{"lang": null, "wants": null}'))
    monkeypatch.setattr(i18n, "translate", lambda msgs, lang: msgs)     # translation itself is tested elsewhere
    i18n._llm_cache.clear()
    assert i18n.analyze("Bana Türkçe konuş") == ("tr", "tr")
    assert i18n.analyze("Habari, nataka kupanda mahindi") == ("sw", None)
    c = TestClient(app)
    out = c.post("/api/chat", json={"phone": "tr1", "text": "Benim dilimde konuş"}).json()["messages"]
    assert "Turkish" in out[0]
    with session() as db:
        assert db.query(Farmer).filter_by(phone="web:tr1").one().language == "tr"

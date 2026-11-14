from fastapi.testclient import TestClient


def _client():
    from app.main import app
    return TestClient(app)


def say(c, phone, **kw):
    return c.post("/api/chat", json={"phone": phone, **kw}).json()["messages"]


def test_full_onboarding_with_pin_then_questions(offline):
    from app.db import Farmer, Observation, session

    c = _client()
    assert "location" in say(c, "a", text="hi")[0].lower()
    msgs = say(c, "a", lat=38.05, lon=-100.75)
    assert "NASA" in msgs[-1] and "Winter wheat → Grain sorghum → Fallow" in msgs[-1]
    assert "Finney County" in msgs[-1]
    assert "Save water" in say(c, "a", text="yes it's dryland")[0]
    msgs = say(c, "a", text="2")
    summary = next(m for m in msgs if "Option 1" in m)
    assert "/r/" in summary
    url = summary.split("http://localhost:8000")[1].split()[0]
    page = c.get(url)
    assert page.status_code == 200 and "Rotation strategies compared" in page.text

    assert "Dryland" in say(c, "a", text="should I water this week?")[0]
    assert "Saved 1.2 in" in say(c, "a", text="we got 1.2 inches of rain")[0]
    assert "Heat" in say(c, "a", text="any heat coming?")[0]
    with session() as db:
        fld = db.query(Farmer).filter_by(phone="web:a").one().fields[-1]
        assert fld.irrigated is False
        assert db.query(Observation).filter_by(field_id=fld.id, kind="rain").one().value == 1.2


def test_sms_zip_onboarding_and_farmer_corrects_crops(offline):
    c = _client()
    say(c, "b", text="hello")
    msgs = say(c, "b", text="my zip is 67846")
    assert "Garden City" in msgs[-1]
    out = say(c, "b", text="no, we grow corn and soybeans, irrigated with a pivot")[0]
    assert "Corn → Soybeans (irrigated)" in out


def test_reset(offline):
    c = _client()
    say(c, "d", text="hi")
    say(c, "d", lat=40.65, lon=-98.3)
    assert "Starting over" in say(c, "d", text="reset")[0]


def test_twilio_webhook_returns_twiml(offline):
    c = _client()
    r = c.post("/twilio/webhook", data={"From": "whatsapp:+15550001", "Body": "hi"})
    assert r.status_code == 200 and "<Message>" in r.text


def test_landing_and_simulator_pages():
    c = _client()
    home = c.get("/")
    assert home.status_code == 200
    assert "reads your field from space" in home.text and 'href="/chat"' in home.text
    assert "https://wa.me/14155238886?text=" in home.text
    assert c.get("/static/landing/hero.jpg").status_code == 200
    assert "WhatsApp simulator" in c.get("/chat").text
    assert c.get("/sample", follow_redirects=False).status_code in (302, 404)


def test_admin_sample_requires_token():
    c = _client()
    assert c.post("/admin/sample", data={"token": "wrong"}).status_code == 403


def test_api_rate_limit(offline):
    from app import ratelimit

    ratelimit.reset()
    c = _client()
    codes = [c.post("/api/chat", json={"phone": "flood", "text": "hello"}).status_code for _ in range(14)]
    assert codes[:12] == [200] * 12 and codes[-1] == 429
    ratelimit.reset()


def test_daily_job_auth_and_refresh(offline, monkeypatch):
    import datetime as dt

    from app.db import Farmer, session

    monkeypatch.setenv("ADMIN_TOKEN", "t0ken")
    c = _client()
    for kw in ({"text": "hi"}, {"lat": 38.05, "lon": -100.75}, {"text": "yes dryland"}, {"text": "2"}):
        say(c, "daily", **kw)
    with session() as db:
        fld = db.query(Farmer).filter_by(phone="web:daily").one().fields[-1]
        fld.features_at = dt.datetime(2020, 1, 1)
        db.commit()
    assert c.post("/internal/daily").status_code == 403
    assert c.post("/internal/daily", headers={"Authorization": "Bearer forged"}).status_code == 403
    r = c.post("/internal/daily", data={"token": "t0ken"})
    assert r.status_code == 200 and r.json()["refreshed"] >= 1


def test_landing_copy_follows_whatsapp_prefill(monkeypatch):
    from app import config

    c = _client()
    monkeypatch.setattr(config, "WHATSAPP_PREFILL", "join shelter-gently")
    page = c.get("/").text
    assert "JOIN ON WHATSAPP" in page and "text=join%20shelter-gently" in page
    monkeypatch.setattr(config, "WHATSAPP_PREFILL", "hi")
    assert "TEXT “HI” ON WHATSAPP" in c.get("/").text

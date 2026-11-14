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
    text = "\n".join(msgs)
    assert "NASA" in text and "Winter wheat → Grain sorghum → Fallow" in text
    assert "Finney County" in text and "Step 2 of 3" in msgs[-1]
    assert "Save water" in "\n".join(say(c, "a", text="yes it's dryland"))
    assert "Add another goal" in say(c, "a", text="2")[0]
    msgs = say(c, "a", text="compare now")
    summary = next(m for m in msgs if "Option 1" in m)
    assert "/r/" in summary
    url = summary.split("http://localhost:8000")[1].split()[0]
    page = c.get(url)
    assert page.status_code == 200 and "Rotation strategies" in page.text and "The bottom line" in page.text

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
    assert "Garden City" in "\n".join(msgs)
    out = say(c, "b", text="no, we grow corn and soybeans, irrigated with a pivot")[0]
    assert "Corn → Soybeans (irrigated)" in out


def test_reset(offline):
    c = _client()
    say(c, "d", text="hi")
    say(c, "d", lat=40.65, lon=-98.3)
    assert "Starting over" in say(c, "d", text="reset")[0]


def test_twilio_webhook_acks_at_once_and_replies_via_api(offline, monkeypatch):
    """The webhook answers Twilio with empty TwiML right away; the reply goes out through the API in the background
    (a reply that takes > 15 s inside the webhook is dropped by Twilio with error 11200)."""
    from app import messaging
    sent = []
    monkeypatch.setattr(messaging, "send", lambda to, m: sent.append((to, m)))
    c = _client()
    r = c.post("/twilio/webhook", data={"From": "whatsapp:+15550001", "Body": "hi", "MessageSid": "SM1"})
    assert r.status_code == 200 and "<Message>" not in r.text
    assert sent and sent[0][0] == "whatsapp:+15550001" and "Step 1 of 3" in sent[0][1]


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
    for kw in ({"text": "hi"}, {"lat": 38.05, "lon": -100.75}, {"text": "yes dryland"}, {"text": "2"}, {"text": "compare now"}):
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
    assert "Scan to join on WhatsApp" in page and "text=join%20shelter-gently" in page
    monkeypatch.setattr(config, "WHATSAPP_PREFILL", "hi")
    page = c.get("/").text
    assert "Text “hi” on WhatsApp" in page and "SEND ME A WHATSAPP" in page and 'name="consent"' in page



def test_buttons_guide_onboarding(offline):
    c = _client()
    r = c.post("/api/chat", json={"phone": "btn", "text": "hi"}).json()
    assert "Step 1 of 3" in r["messages"][0] and r["actions"] == [None]
    r = c.post("/api/chat", json={"phone": "btn", "lat": 38.05, "lon": -100.75}).json()
    crops = r["actions"][-1]
    assert [b["id"] for b in crops["buttons"]] == ["yes irrigated", "yes dryland", "different crops"]
    r = c.post("/api/chat", json={"phone": "btn", "text": "different crops"}).json()  # tapped "Different crops"
    assert "which crops" in r["messages"][0].lower()
    r = c.post("/api/chat", json={"phone": "btn", "text": "sorghum, wheat"}).json()   # irrigation unknown -> asked
    assert [b["id"] for b in r["actions"][-1]["buttons"]] == ["irrigated", "dryland"]
    r = c.post("/api/chat", json={"phone": "btn", "text": "dryland"}).json()
    goals = r["actions"][-1]["list"]["items"]
    assert [g["id"] for g in goals] == ["1", "2", "3", "4", "5", "6"]
    r = c.post("/api/chat", json={"phone": "btn", "text": "2"}).json()
    assert [b["id"] for b in r["actions"][-1]["buttons"]] == ["compare now", "add goal"]
    r = c.post("/api/chat", json={"phone": "btn", "text": "add goal"}).json()
    assert [g["id"] for g in r["actions"][-1]["list"]["items"]] == ["1", "3", "4", "5", "6"]   # minus the chosen one
    r = c.post("/api/chat", json={"phone": "btn", "text": "1"}).json()                          # 2nd pick → compare
    assert r["actions"][-1]["buttons"][2]["id"] == "report"
    assert "/r/" in c.post("/api/chat", json={"phone": "btn", "text": "report"}).json()["messages"][0]


def test_webhook_sends_button_turns_via_api(offline, monkeypatch):
    from app import main, messaging

    sent = []
    monkeypatch.setattr(messaging, "send", lambda to, m: sent.append(m))
    c = _client()
    c.post("/twilio/webhook", data={"From": "whatsapp:+15550009", "Body": "hi"})
    r = c.post("/twilio/webhook", data={"From": "whatsapp:+15550009", "Latitude": "38.05", "Longitude": "-100.75"})
    assert r.status_code == 200
    assert any(isinstance(m, dict) and "buttons" in m for m in sent)
    sent.clear()
    c.post("/twilio/webhook", data={"From": "whatsapp:+15550009", "Body": "Yes, dryland", "ButtonPayload": "yes dryland"})
    assert any(isinstance(m, dict) and "list" in m for m in sent)  # the goal picker


def test_goals_typed_together_and_in_own_words(offline):
    from app.db import Farmer, session
    c = _client()

    def setup(p):
        for kw in ({"text": "hi"}, {"lat": 38.05, "lon": -100.75}, {"text": "yes dryland"}):
            say(c, p, **kw)

    setup("g1")
    assert "Scoring" in say(c, "g1", text="water and soil please")[0]          # several at once → straight to compare
    setup("g2")
    assert "Tell me your goal" in say(c, "g2", text="6")[0]
    out = say(c, "g2", text="I want to use less fertilizer")
    assert out[0].startswith("📝 Noted your goal") and "Scoring" in out[1]
    with session() as db:
        p1 = db.query(Farmer).filter_by(phone="web:g1").one()
        p2 = db.query(Farmer).filter_by(phone="web:g2").one()
        assert p1.priorities == ["save_water", "soil_health"] and p1.state == "ready"
        assert p2.constraints["goal_note"] == "I want to use less fertilizer" and p2.priorities and p2.state == "ready"

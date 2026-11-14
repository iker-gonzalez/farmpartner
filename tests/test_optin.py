from fastapi.testclient import TestClient


def _client():
    from app.main import app
    return TestClient(app)


def say(c, phone, **kw):
    return c.post("/api/chat", json={"phone": phone, **kw}).json()["messages"]


def test_optin_validates_and_falls_back_to_wa_link(offline):
    c = _client()
    assert c.post("/api/optin", json={"phone": "12", "consent": True}).status_code == 400
    assert c.post("/api/optin", json={"phone": "+1 620 555 0100", "consent": False}).status_code == 400
    r = c.post("/api/optin", json={"phone": "+1 620 555 0100", "consent": True}).json()
    assert r["sent"] is False and r["wa_url"].startswith("https://wa.me/")


def test_optin_sends_template_once_per_day(offline, monkeypatch):
    from app import messaging
    from app.db import Farmer, session
    sent = []
    monkeypatch.setattr(messaging, "welcome_status", lambda: "approved")
    monkeypatch.setattr(messaging, "send_welcome", lambda to: sent.append(to) or True)
    c = _client()
    assert c.post("/api/optin", json={"phone": "+1 (620) 555-0101", "consent": True}).json() == {"sent": True}
    assert c.post("/api/optin", json={"phone": "+16205550101", "consent": True}).json()["again"] is True
    assert sent == ["whatsapp:+16205550101"]
    with session() as db:
        fm = db.query(Farmer).filter_by(phone="whatsapp:+16205550101").one()
        assert fm.state == "new" and fm.constraints["opted_in_at"]


def test_template_buttons_and_stop(offline):
    c = _client()
    assert "anytime" in say(c, "t1", text="not now")[0]
    assert "Step 1 of 3" in say(c, "t1", text="get started")[0]
    assert "unsubscribed" in say(c, "t2", text="STOP")[0]
    from app.db import Farmer, session
    with session() as db:
        assert db.query(Farmer).filter_by(phone="web:t2").one().constraints["opted_out"] is True
    say(c, "t2", text="hi")
    with session() as db:
        assert db.query(Farmer).filter_by(phone="web:t2").one().constraints["opted_out"] is False

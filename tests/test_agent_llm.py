"""Exercise the Claude tool loop with a fake client (no network, no API key)."""
from types import SimpleNamespace as NS


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        if len(self.calls) == 1:
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="irrigation_advice", input={})])
        tool_result = kw["messages"][-1]["content"][0]
        assert tool_result["type"] == "tool_result" and "advice" in tool_result["content"]
        return NS(stop_reason="end_turn", content=[NS(type="text", text="💧 Water about 1.9 in this week.")])


def test_llm_tool_loop(offline, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    c = TestClient(app)
    for kw in ({"text": "hi"}, {"lat": 40.65, "lon": -98.3}, {"text": "yes irrigated"}, {"text": "1"}):
        c.post("/api/chat", json={"phone": "llm", **kw})

    fake = FakeMessages()
    monkeypatch.setattr(config, "llm_enabled", lambda: True)
    from app import agent
    monkeypatch.setattr(agent, "_client", NS(beta=NS(messages=fake)))
    out = c.post("/api/chat", json={"phone": "llm", "text": "do I need to water?"}).json()["messages"]
    assert out == ["💧 Water about 1.9 in this week."]
    first = fake.calls[0]
    assert first["model"] == config.ANTHROPIC_MODEL and first["fallbacks"] == "default"
    assert first["messages"][-1] == {"role": "user", "content": "do I need to water?"}
    assert first["messages"][0]["role"] == "user"


def test_photo_request_ends_on_the_farmers_turn_with_the_image(offline, monkeypatch):
    """Regression: the "📷 Got it — taking a look…" reply is logged first; the request must still end on the user."""
    from fastapi.testclient import TestClient

    from app import agent, config, media, messaging
    from app.db import Farmer, session
    from app.main import app

    c = TestClient(app)
    for kw in ({"text": "hi"}, {"lat": 40.65, "lon": -98.3}, {"text": "yes irrigated"}, {"text": "1"}):
        c.post("/api/chat", json={"phone": "pic", **kw})
    with session() as db:  # move the web farmer onto WhatsApp so the webhook finds it onboarded
        db.query(Farmer).filter_by(phone="web:pic").one().phone = "whatsapp:+15550002222"
        db.commit()

    calls = []

    def create(**kw):
        calls.append(kw["messages"])
        return NS(stop_reason="end_turn", content=[NS(type="text", text="Looks like northern corn leaf blight.")])

    sent = []
    monkeypatch.setattr(config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent, "_client", NS(beta=NS(messages=NS(create=create))))
    monkeypatch.setattr(messaging, "send", lambda to, m: sent.append(m))
    monkeypatch.setattr(media, "download", lambda url: (b"\xff\xd8jpeg", "image/jpeg"))
    c.post("/twilio/webhook", data={"From": "whatsapp:+15550002222", "Body": "Is my corn healthy?",
                                    "MediaUrl0": "u", "MediaContentType0": "image/jpeg", "NumMedia": "1"})
    last = calls[0][-1]
    assert last["role"] == "user" and last["content"][0]["type"] == "image"
    assert "Is my corn healthy?" in last["content"][1]["text"]
    assert sent == ["📷 Got it — taking a look…", "Looks like northern corn leaf blight."]

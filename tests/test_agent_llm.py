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

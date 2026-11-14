"""One entry point for every channel (WhatsApp, SMS, web simulator, CLI)."""
from dataclasses import dataclass
from typing import Callable

from app import agent, onboarding, service
from app.prompts import as_text
from app.db import session


@dataclass
class Turn:
    now: list[str]
    later: Callable[[], list[str]] | None


def inbound(phone: str, channel: str, text: str, lat: float | None = None, lon: float | None = None,
            media_url: str | None = None) -> Turn:
    with session() as db:
        farmer = service.get_or_create_farmer(db, phone, channel)
    onboarding.record(farmer.id, "in", text or "", lat, lon, media_url)

    if (text or "").strip().lower() == "report":
        with session() as db:
            from app.db import Farmer
            fld = service.main_field(db.get(Farmer, farmer.id))
            if fld:
                return _out(farmer.id, Turn(now=[f"📊 Your field report: {service.report_url(fld)}"], later=None))

    if lat is not None and farmer.state == "ready":
        farmer.state = "awaiting_location"  # a new pin from an onboarded farmer = add/replace the field
    reply = onboarding.handle(farmer, text or "", lat, lon)
    if reply is None:  # onboarded: free conversation, answered in the background (LLM can take a few seconds)
        def later():
            return [agent.answer(farmer, text or "")]
        return _out(farmer.id, Turn(now=[], later=later))
    return _out(farmer.id, Turn(now=reply.now, later=reply.later))


def _out(farmer_id: int, turn: Turn) -> Turn:
    """Wrap the turn so every outbound message is logged (the agent reads this history)."""
    for m in turn.now:
        onboarding.record(farmer_id, "out", as_text(m))
    if turn.later is None:
        return turn
    inner = turn.later

    def later():
        msgs = inner()
        for m in msgs:
            onboarding.record(farmer_id, "out", as_text(m))
        return msgs

    return Turn(now=turn.now, later=later)

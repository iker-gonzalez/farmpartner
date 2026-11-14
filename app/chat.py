"""One entry point for every channel (WhatsApp, SMS, web simulator, CLI)."""
import re
from dataclasses import dataclass
from typing import Callable

from app import agent, i18n, onboarding, service
from app.data import maplinks
from app.prompts import as_text
from app.db import now, session


@dataclass
class Turn:
    now: list[str]
    later: Callable[[], list[str]] | None


def inbound(phone: str, channel: str, text: str, lat: float | None = None, lon: float | None = None,
            media_url: str | None = None) -> Turn:
    with session() as db:
        farmer = service.get_or_create_farmer(db, phone, channel)
    onboarding.record(farmer.id, "in", text or "", lat, lon, media_url)
    lang = i18n.detect(text or "")
    if lang and lang != farmer.language:
        with session() as db:
            from app.db import Farmer
            db.get(Farmer, farmer.id).language = lang
            db.commit()
        farmer.language = lang
    if i18n.is_switch(text or ""):
        reply = onboarding.current_step(farmer)
        hello = "👍 De acuerdo, seguimos en español." if farmer.language == "es" else "👍 Sure, let's continue in English."
        return _out(farmer.id, Turn(now=[hello] + reply, later=None), farmer.language)

    if (text or "").strip().lower() == "report":
        with session() as db:
            from app.db import Farmer
            fld = service.main_field(db.get(Farmer, farmer.id))
            if fld:
                return _out(farmer.id, Turn(now=[f"📊 Your field report: {service.report_url(fld)}"], later=None))

    if lat is None and re.search(r"https?://|\d\.\d+\s*[,;]", text or ""):
        pt = maplinks.location_from_text(text)
        if pt:  # a pasted Google/Apple Maps link or "lat, lon" counts as a pin
            lat, lon, text = pt[0], pt[1], ""
    if lat is not None and farmer.state not in ("new", "awaiting_location"):
        with session() as db:
            from app.db import Farmer
            last = service.main_field(db.get(Farmer, farmer.id))
            dup = (last is not None and abs(last.lat - lat) < 1e-4 and abs(last.lon - lon) < 1e-4
                   and (now() - last.created_at).total_seconds() < 300)
        if dup:  # WhatsApp sometimes delivers the same pin twice; the first one is already being analyzed
            return Turn(now=[], later=None)
        farmer.state = "awaiting_location"  # a new pin at any step = add/replace the field
    reply = onboarding.handle(farmer, text or "", lat, lon)
    if reply is None:  # onboarded: free conversation, answered in the background (LLM can take a few seconds)
        def later():
            return [agent.answer(farmer, text or "")]
        return _out(farmer.id, Turn(now=[], later=later))  # Claude already answers in the farmer's language
    return _out(farmer.id, Turn(now=reply.now, later=reply.later), farmer.language)


def photo(phone: str, channel: str, image: dict | None, caption: str, media_url: str | None = None) -> Turn:
    """A photo from the farmer (WhatsApp). Read by Claude once the field is set up."""
    with session() as db:
        farmer = service.get_or_create_farmer(db, phone, channel)
    onboarding.record(farmer.id, "in", ("[sent a photo] " + caption).strip(), media_url=media_url)
    if farmer.state != "ready":
        return _out(farmer.id, Turn(now=["📷 Got your photo! I can look at crop photos once your field is set up — "
                                         "let's finish that first. Send RESET if you want to start over."], later=None),
                    farmer.language)
    if image is None:
        return _out(farmer.id, Turn(now=["📷 I couldn't open that file. Please send it as a normal photo (JPG or PNG)."],
                                    later=None), farmer.language)

    def later():
        return [agent.answer(farmer, caption, image=image)]
    return _out(farmer.id, Turn(now=i18n.translate(["📷 Got it — taking a look…"], farmer.language), later=later))


def _out(farmer_id: int, turn: Turn, lang: str = "en") -> Turn:
    """Wrap the turn so every outbound message is logged (the agent reads this history). lang="es" translates
    FarmPartner's scripted messages into Spanish."""
    if lang != "en":
        turn = Turn(now=i18n.translate(turn.now, lang), later=turn.later)
        if turn.later:
            inner_tr = turn.later
            turn = Turn(now=turn.now, later=lambda: i18n.translate(inner_tr(), lang))
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

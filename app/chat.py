"""One entry point for every channel (WhatsApp, SMS, web simulator, CLI)."""
import re
import time
from dataclasses import dataclass
from typing import Callable

from app import agent, i18n, onboarding, service, units, voice
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
    lang, wants = (None, None) if (text or "").strip().lower() in onboarding.button_ids() else i18n.analyze(text or "")
    new_lang = wants or lang
    if new_lang and new_lang != farmer.language:
        with session() as db:
            from app.db import Farmer
            db.get(Farmer, farmer.id).language = new_lang
            db.commit()
        farmer.language = new_lang
    if wants and lat is None and len((text or "").split()) <= 12:
        hello = ("👍 Sure, let's continue in English." if farmer.language == "en"
                 else f"👍 Sure — I'll write to you in {i18n.LANGS.get(farmer.language, farmer.language)}.")
        return _out(farmer.id, Turn(now=[hello] + onboarding.current_step(farmer), later=None), farmer.language,
                    units.preferred(farmer))
    want_voice = voice.asked(text or "") if lat is None and not i18n.explicit_language(text or "") else None
    if want_voice is not None and not onboarding.is_question(text or ""):
        _set_voice(farmer, want_voice)
        msg = ("🔊 Done — I'll also send my answers as voice notes. Say \"text only\" to stop." if want_voice
               else "👍 OK — text only from now on.")
        step = onboarding.current_step(farmer) if farmer.state not in ("ready",) else []
        return _out(farmer.id, Turn(now=[msg] + step, later=None), farmer.language, units.preferred(farmer))
    want_units = units.asked(text or "") if lat is None else None
    if want_units:
        with session() as db:
            from app.db import Farmer
            fm = db.get(Farmer, farmer.id)
            fm.constraints = {**(fm.constraints or {}), "units": want_units}
            db.commit()
            farmer.constraints = fm.constraints
        hello = ("👍 Got it — I'll use metric units: mm of rain and water, °C, hectares." if want_units == "metric"
                 else "👍 Got it — I'll use inches, °F and acres.")
        step = onboarding.current_step(farmer) if farmer.state != "ready" else []
        return _out(farmer.id, Turn(now=[hello] + step, later=None), farmer.language, want_units)

    if (text or "").strip().lower() == "report":
        with session() as db:
            from app.db import Farmer
            fld = service.main_field(db.get(Farmer, farmer.id))
            if fld:
                return _out(farmer.id, Turn(now=[f"📊 Your field report: {service.report_url(fld)}"], later=None),
                            farmer.language, units.preferred(farmer))

    if lat is None and re.search(r"https?://|\d\.\d+\s*[,;]|\d\s*°", text or ""):
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
    if lat is None and farmer.state in onboarding.MID_ONBOARDING and onboarding.is_question(text or ""):
        # A question in the middle of setup ("which crops suit this area?"): Claude answers it, then we repeat
        # the step we're waiting for, so the farmer isn't stuck.
        unit, lang = units.preferred(farmer), farmer.language

        def answer_then_step():
            step = onboarding.current_step(farmer) if _remind(farmer.id) else []
            return [agent.answer(farmer, text or "", image=recent_photo(farmer.id))] + i18n.translate(
                units.convert(step) if unit == "metric" else step, lang)
        return _out(farmer.id, Turn(now=[], later=answer_then_step))
    reply = onboarding.handle(farmer, text or "", lat, lon)
    if reply is not None and reply.to_agent:
        unit, lang = units.preferred(farmer), farmer.language

        def agent_then_step():
            before = _rotation(farmer.id)
            said = agent.answer(farmer, text or "", image=recent_photo(farmer.id))
            moved = _advance_if_rotation_saved(farmer.id, before)
            step = onboarding.current_step(moved or farmer) if (moved or _remind(farmer.id)) else []
            return [said] + i18n.translate(units.convert(step) if unit == "metric" else step, lang)
        return _out(farmer.id, Turn(now=[], later=agent_then_step))
    if reply is None:  # onboarded: free conversation, answered in the background (LLM can take a few seconds)
        def later():
            return [agent.answer(farmer, text or "", image=recent_photo(farmer.id))]
        return _out(farmer.id, Turn(now=[], later=later))  # Claude already answers in the farmer's language and units
    return _out(farmer.id, Turn(now=reply.now, later=reply.later), farmer.language, units.preferred(farmer))


def _rotation(farmer_id: int):
    from app.db import Farmer
    with session() as db:
        fld = service.main_field(db.get(Farmer, farmer_id))
        return list(fld.current_rotation or []) if fld else None


def _advance_if_rotation_saved(farmer_id: int, before):
    """Claude can save the farmer's rotation with a tool ("I choose the first one"). If that happened while setup
    was still waiting for the crops, go to the next step; returns the refreshed farmer, else None."""
    from app.db import Farmer
    with session() as db:
        fm = db.get(Farmer, farmer_id)
        fld = service.main_field(fm)
        if fm.state != "awaiting_crops" or fld is None or not fld.current_rotation or list(fld.current_rotation) == before:
            return None
        fm.state = "awaiting_irrigation" if fld.irrigated is None else "awaiting_priority"
        db.commit()
        db.refresh(fm)
        _ = list(fm.priorities or [])
        db.expunge(fm)
        return fm


_photos: dict[int, tuple[float, dict]] = {}
_reminded: dict[int, float] = {}
PHOTO_MEMORY_S = 600
REMIND_EVERY_S = 180


def remember_photo(farmer_id: int, image: dict | None) -> None:
    if image is not None:
        _photos[farmer_id] = (time.time(), image)


def recent_photo(farmer_id: int) -> dict | None:
    """The farmer's photo from the last 10 minutes, so "how does my barley look?" right after it can see it.
    Used once."""
    t, img = _photos.get(farmer_id, (0.0, None))
    if img is not None and time.time() - t <= PHOTO_MEMORY_S:
        _photos.pop(farmer_id, None)
        return img
    return None


def _remind(farmer_id: int) -> bool:
    """Repeat the setup step after an answer at most every few minutes, not after every single question."""
    now_t = time.time()
    if now_t - _reminded.get(farmer_id, 0) < REMIND_EVERY_S:
        return False
    _reminded[farmer_id] = now_t
    return True


def _set_voice(farmer, on: bool) -> None:
    from app.db import Farmer
    with session() as db:
        fm = db.get(Farmer, farmer.id)
        fm.constraints = {**(fm.constraints or {}), "voice_replies": on}
        db.commit()
        farmer.constraints = fm.constraints


def apply_locale(phone: str, channel: str, lang: str | None, unit: str | None) -> None:
    """First visit from a browser: start in the browser's language and units. Once only, never over a choice."""
    from app.db import Farmer
    with session() as db:
        fm = service.get_or_create_farmer(db, phone, channel)
        c = dict(fm.constraints or {})
        if c.get("locale_set"):
            return
        if lang in i18n.LANGS:
            fm.language = lang
        if unit in ("metric", "imperial") and "units" not in c:
            c["units"] = unit
        c["locale_set"] = True
        fm.constraints = c
        db.commit()


def wants_voice(phone: str) -> tuple[bool, str]:
    """(spoken replies on?, language) for this farmer."""
    from app.db import Farmer
    with session() as db:
        fm = db.query(Farmer).filter(Farmer.phone == phone).first()
        if not fm:
            return False, "en"
        return bool((fm.constraints or {}).get("voice_replies")), fm.language or "en"


def speech_languages(phone: str) -> list[str]:
    """Languages to listen for in a voice note: the farmer's, their phone country's, then English."""
    from app.data.geocode import phone_country
    from app.db import Farmer
    with session() as db:
        fm = db.query(Farmer).filter(Farmer.phone == phone).first()
        lang = fm.language if fm else "en"
    by_country = {"es": ["es", "eu", "ca"], "mx": ["es"], "ar": ["es"], "co": ["es"], "cl": ["es"], "fr": ["fr"],
                  "pt": ["pt"], "br": ["pt"], "de": ["de"], "it": ["it"], "jp": ["ja"], "in": ["hi", "en"],
                  "ph": ["tl", "en"], "tr": ["tr"], "pl": ["pl"], "nl": ["nl"], "th": ["th"], "za": ["af", "en"],
                  "ro": ["ro"], "hu": ["hu"], "cz": ["cs"], "se": ["sv"]}.get(phone_country(phone), [])
    return list(dict.fromkeys([lang] + by_country + ["en", "es"]))


def photo(phone: str, channel: str, image: dict | None, caption: str, media_url: str | None = None) -> Turn:
    """A photo from the farmer (WhatsApp). Read by Claude once the field is set up."""
    with session() as db:
        farmer = service.get_or_create_farmer(db, phone, channel)
    onboarding.record(farmer.id, "in", ("[sent a photo] " + caption).strip(), media_url=media_url)
    cap_lang = i18n.detect(caption or "")
    if cap_lang and cap_lang != farmer.language:
        with session() as db:
            from app.db import Farmer
            db.get(Farmer, farmer.id).language = cap_lang
            db.commit()
        farmer.language = cap_lang
    if farmer.state != "ready" or not (caption or "").strip():
        remember_photo(farmer.id, image)  # a question right after the photo will get to see it
    if farmer.state == "awaiting_crops" and image is not None:
        # A photo at the crop step answers "what do you grow?": identify the crop and carry on with setup.
        unit, lang = units.preferred(farmer), farmer.language

        def from_photo():
            name = agent.identify_crop(image)
            if not name:
                with session() as db:
                    from app.db import Farmer
                    fld = service.main_field(db.get(Farmer, farmer.id))
                    example = onboarding.crop_example(fld.features if fld else None)
                msgs = [f"📷 I couldn't tell the crop from the photo — please type it (e.g. \"{example}\")."]
            else:
                r = onboarding.handle(farmer, name, None, None)
                msgs = [f"📷 That looks like *{name}*."] + ((r.now + (r.later() if r.later else [])) if r else [])
            return i18n.translate(units.convert(msgs) if unit == "metric" else msgs, lang)
        return _out(farmer.id, Turn(now=[], later=from_photo))
    if farmer.state != "ready":
        return _out(farmer.id, Turn(now=["📷 Got your photo! I can look at crop photos once your field is set up — "
                                         "let's finish that first. Send RESET if you want to start over."], later=None),
                    farmer.language, units.preferred(farmer))
    if image is None:
        return _out(farmer.id, Turn(now=["📷 I couldn't open that file. Please send it as a normal photo (JPG or PNG)."],
                                    later=None), farmer.language, units.preferred(farmer))

    def later():
        return [agent.answer(farmer, caption, image=image)]
    return _out(farmer.id, Turn(now=i18n.translate(["📷 Got it — taking a look…"], farmer.language), later=later))


def _is_web(farmer_id: int) -> bool:
    from app.db import Farmer
    with session() as db:
        fm = db.get(Farmer, farmer_id)
        return bool(fm and fm.phone.startswith(("web:", "cli:")))


def _web_howto(msgs: list) -> list:
    return [m.replace(onboarding.PIN_HOWTO, onboarding.WEB_PIN_HOWTO) if isinstance(m, str) else m for m in msgs]


def _out(farmer_id: int, turn: Turn, lang: str = "en", unit: str = "imperial") -> Turn:
    """Wrap the turn so every outbound message is logged (the agent reads this history). FarmPartner's scripted
    messages are converted to metric (deterministically) and translated into the farmer's language."""
    if _is_web(farmer_id):  # the web chat can't drag a map like WhatsApp: swap in its own pin instructions
        turn = Turn(now=_web_howto(turn.now), later=turn.later)
        if turn.later:
            inner_web = turn.later
            turn = Turn(now=turn.now, later=lambda: _web_howto(inner_web()))
    if lang != "en" or unit == "metric":
        def localize(msgs):
            return i18n.translate(units.convert(msgs) if unit == "metric" else msgs, lang)
        turn = Turn(now=localize(turn.now), later=turn.later)
        if turn.later:
            inner_tr = turn.later
            turn = Turn(now=turn.now, later=lambda: localize(inner_tr()))
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

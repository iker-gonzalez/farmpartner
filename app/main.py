import datetime as dt
import logging
import os
import re
import threading

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select

from app import chat, config, i18n, landing, site_i18n, media, messaging, ratelimit, report_view, service, sources, team, voice
from app.prompts import as_text, is_prompt
from app.db import Farmer, Field, init_db, session
from app.engine.explain import DISCLAIMER, SCORE_LABELS, evidence, gaps, inch, short_name
from app.engine.scoring import PRIORITY_LABELS
from app.engine.features import crop_name, rotation_label

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farmpartner")
app = FastAPI(title="FarmPartner")
templates = Jinja2Templates(directory=str(config.ROOT / "app" / "templates"))
app.mount("/static", StaticFiles(directory=str(config.ROOT / "app" / "static")), name="static")


@app.middleware("http")
async def _site_language(request: Request, call_next):
    """Public pages: serve them in the visitor's language (?lang= or the fp_lang cookie; English by default)."""
    if request.method != "GET" or request.url.path not in site_i18n.PAGES:
        return await call_next(request)
    lang = site_i18n.pick(request)
    request.state.lang = lang
    resp = await call_next(request)
    if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("text/html"):
        body = b"".join([chunk async for chunk in resp.body_iterator]).decode()
        headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
        resp = HTMLResponse(site_i18n.localize(body, lang), status_code=200, headers=headers)
        resp.headers["Vary"] = "Cookie"
    if request.query_params.get("lang") in site_i18n.LANGS:
        resp.set_cookie(site_i18n.COOKIE, lang, max_age=365 * 24 * 3600, samesite="lax")
    return resp

init_db()


@app.on_event("startup")
def _warm_up():
    if os.getenv("WARMUP", "1") == "1":
        from app import warmup
        warmup.start()


def _twiml(messages: list[str]) -> Response:
    from twilio.twiml.messaging_response import MessagingResponse

    r = MessagingResponse()
    for m in messages:
        r.message(m)
    return Response(str(r), media_type="application/xml")


def _run_later(to: str, later, message_sid: str | None = None, first_after_s: float = 2) -> list:
    """Run the slow part of a turn and send its messages; returns them (for the spoken version)."""
    try:
        with messaging.typing_while(message_sid, first_after_s=first_after_s):  # "typing…" while NASA data or Claude is working
            msgs = later()
        for m in msgs:
            messaging.send(to, m)
        return msgs
    except Exception:
        log.exception("background job failed")
        messaging.send(to, "Sorry — something went wrong fetching your data. Please try again in a minute.")
        return []


def _speak(to: str, msgs: list, force: bool = False) -> None:
    """Also send the turn as a voice note when the farmer wants it (or just sent one). Never breaks the chat."""
    if not to.startswith("whatsapp:") or not msgs:
        return
    try:
        on, lang = chat.wants_voice(to)
        if not (on or force):
            return
        text = voice.speakable(msgs)
        if len(text) < 2:
            return
        messaging.send_media(to, voice.store(voice.synthesize(text, lang)))
    except Exception:
        log.exception("spoken reply failed")


@app.get("/voice/{vid}.ogg", include_in_schema=False)
def voice_clip(vid: str):
    audio = voice.load(vid)
    if audio is None:
        raise HTTPException(404)
    return Response(audio, media_type="audio/ogg", headers={"Cache-Control": "public, max-age=86400"})


@app.post("/twilio/webhook")
async def twilio_webhook(request: Request, background: BackgroundTasks):
    form = dict(await request.form())
    if config.TWILIO_AUTH_TOKEN:
        from twilio.request_validator import RequestValidator

        url = config.PUBLIC_BASE_URL + "/twilio/webhook"
        if not RequestValidator(config.TWILIO_AUTH_TOKEN).validate(url, form, request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(403, "invalid Twilio signature")
    sender = form.get("From", "")
    if not ratelimit.allow("wh:" + sender, ratelimit.WEBHOOK_PER_SENDER):
        return _twiml([])  # silently drop floods; no outbound message, no cost
    channel = "whatsapp" if sender.startswith("whatsapp:") else "sms"
    lat = float(form["Latitude"]) if form.get("Latitude") else None
    lon = float(form["Longitude"]) if form.get("Longitude") else None
    # A tapped button / list item arrives with its id; that id is the text the conversation understands.
    text = form.get("ButtonPayload") or form.get("ListId") or form.get("Body", "")
    media_url, media_kind = form.get("MediaUrl0"), media.kind(form.get("MediaContentType0"))
    if media_url and media_kind:  # voice notes and photos need downloading and processing: do it in the background
        background.add_task(_media_turn, sender, channel, media_url, media_kind, text, form.get("MessageSid"))
        return _twiml([])
    # Everything else is answered in the background too and sent through the API: Twilio drops a webhook reply
    # after 15 s, and a first-time translation or a slow lookup can take longer than that.
    background.add_task(_text_turn, sender, channel, text, lat, lon, form.get("MessageSid"))
    return _twiml([])


def _text_turn(sender: str, channel: str, text: str, lat, lon, message_sid: str | None = None):
    import time
    t0 = time.monotonic()
    with messaging.typing_while(message_sid, first_after_s=0):  # "typing…" until the first reply is ready
        try:
            turn = chat.inbound(sender, channel, text, lat, lon)
        except Exception:
            log.exception("inbound message failed")
            messaging.send(sender, "Sorry — something went wrong on my side. Please try again in a minute.")
            return
    t_now = time.monotonic() - t0
    for m in turn.now:
        messaging.send(sender, m)
    sent = list(turn.now) + (_run_later(sender, turn.later, message_sid) if turn.later else [])
    _speak(sender, sent)
    log.info("turn timing: first reply ready %.1fs, done %.1fs", t_now, time.monotonic() - t0)


def _media_turn(sender: str, channel: str, url: str, kind: str, caption: str, message_sid: str | None = None):
    messaging.typing(message_sid)
    try:
        data, content_type = media.download(url)
    except Exception:
        log.exception("media download failed")
        messaging.send(sender, "Sorry, I couldn't open that file. Could you send it again?")
        return
    if kind == "audio":
        try:
            heard = media.transcribe(data, content_type, chat.speech_languages(sender))
        except Exception:
            log.exception("transcription failed")
            heard = None
        if not heard:
            messaging.send(sender, "🎙️ Sorry, I couldn't make out that voice note. Could you type it, or send a "
                                   "shorter note (under a minute)?")
            return
        heard_lang = i18n.analyze(heard)[0] or chat.wants_voice(sender)[1]  # the note's language, else the farmer's
        messaging.send(sender, "🎙️ " + i18n.translate(["I heard:"], heard_lang)[0] + f" “{heard}”")
        turn = chat.inbound(sender, channel, heard, None, None, url)
    else:
        turn = chat.photo(sender, channel, media.image_block(data, content_type), caption, url)
    for m in turn.now:
        messaging.send(sender, m)
    sent = list(turn.now) + (_run_later(sender, turn.later, message_sid) if turn.later else [])
    _speak(sender, sent, force=(kind == "audio"))  # a voice note gets a voice note back


class ChatIn(BaseModel):
    phone: str
    text: str = ""
    lat: float | None = None
    lon: float | None = None
    wait: bool = True  # False: return the immediate reply now and deliver slow follow-ups via /api/poll
    locale: str | None = None  # the browser's language tag ("es-ES"): first-visit language and units


_outbox: dict[str, list[str]] = {}
_audio: dict[str, list[str]] = {}
_pending: dict[str, int] = {}
_lock = threading.Lock()


def _web_voice(key: str, msgs: list, force: bool = False) -> str | None:
    """The spoken version of a web turn (same rules as WhatsApp): a /voice/… path, or None."""
    try:
        on, lang = chat.wants_voice(key)
        if not (on or force) or not msgs:
            return None
        text = voice.speakable(msgs)
        if len(text) < 2:
            return None
        url = voice.store(voice.synthesize(text, lang))
        return url[url.index("/voice/"):]
    except Exception:
        log.exception("spoken reply failed")
        return None


def _deliver_later(key: str, later, spoken: list | None = None, force: bool = False):
    try:
        msgs = later()
    except Exception:
        log.exception("background job failed")
        msgs = ["Sorry — something went wrong fetching your data. Please try again in a minute."]
    clip = _web_voice(key, (spoken or []) + list(msgs), force)
    with _lock:
        _outbox.setdefault(key, []).extend(msgs)
        if clip:
            _audio.setdefault(key, []).append(clip)
        _pending[key] -= 1


def _api_messages(msgs: list, pending: bool = False, audio: list | None = None) -> dict:
    """messages: plain text (what SMS would show); actions: the tap-able choices for the simulator, if any;
    audio: spoken replies (voice-note clips) to play after the messages."""
    return {"messages": [as_text(m) for m in msgs],
            "actions": [({k: v for k, v in m.items() if k != "text"} | {"text": m["text"]}) if is_prompt(m) else None
                        for m in msgs],
            "audio": [a for a in (audio or []) if a],
            "pending": pending}


def _web_turn(key: str, turn, wait: bool, before: list | None = None, force: bool = False) -> dict:
    """Answer a web turn: the quick part now, the slow part now (wait) or later through /api/poll.
    before: messages shown first but not spoken (the "I heard" echo)."""
    out = list(turn.now)
    if turn.later and wait:
        out += turn.later()
        return _api_messages((before or []) + out, audio=[_web_voice(key, out, force)])
    if turn.later:
        with _lock:
            _pending[key] = _pending.get(key, 0) + 1
        threading.Thread(target=_deliver_later, args=(key, turn.later, out, force), daemon=True).start()
        return _api_messages((before or []) + out, pending=True)
    return _api_messages((before or []) + out, audio=[_web_voice(key, out, force)],
                         pending=_pending.get(key, 0) > 0)


def _web_locale(key: str, locale: str | None) -> None:
    """"es-ES" → Spanish and metric; "en-US" → English and imperial. Applied once, on a farmer's first visit."""
    if not locale or len(locale) > 35:
        return
    parts = re.split(r"[-_]", locale)
    lang = parts[0].lower()
    region = next((p.upper() for p in parts[1:] if len(p) == 2), None)
    unit = None if region is None else ("imperial" if region in ("US", "LR", "MM") else "metric")
    try:
        chat.apply_locale(key, "web", lang, unit)
    except Exception:
        log.exception("locale not applied")


def _check_web(phone: str, request: Request) -> str:
    if len(phone) > 64:
        raise HTTPException(400, "message too long")
    if not (ratelimit.allow("ip:" + ratelimit.client_ip(request), ratelimit.API_PER_IP)
            and ratelimit.allow("ph:" + phone, ratelimit.API_PER_PHONE)):
        raise HTTPException(429, "Too many messages — please wait a minute.")
    return "web:" + phone


@app.post("/api/chat")
def api_chat(msg: ChatIn, request: Request):
    """Chat for the web simulator, tests and demos without WhatsApp."""
    if len(msg.text) > 1000:
        raise HTTPException(400, "message too long")
    key = _check_web(msg.phone, request)
    _web_locale(key, msg.locale)
    return _web_turn(key, chat.inbound(key, "web", msg.text, msg.lat, msg.lon), msg.wait)


class MediaIn(BaseModel):
    phone: str
    kind: str  # "audio" (a recorded voice note) or "image"
    data: str  # base64
    content_type: str
    caption: str = ""
    wait: bool = False
    locale: str | None = None


@app.post("/api/media")
def api_media(msg: MediaIn, request: Request):
    """A voice note or photo from the browser demo, handled like the WhatsApp ones."""
    import base64
    import binascii

    if msg.kind not in ("audio", "image") or len(msg.data) > 8_000_000 or len(msg.caption) > 1000:
        raise HTTPException(400, "file too large")
    key = _check_web(msg.phone, request)
    _web_locale(key, msg.locale)
    try:
        raw = base64.b64decode(msg.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "bad file")
    if msg.kind == "image":
        turn = chat.photo(key, "web", media.image_block(raw, msg.content_type), msg.caption)
        return _web_turn(key, turn, msg.wait)
    try:
        heard = media.transcribe(raw, msg.content_type, chat.speech_languages(key))
    except Exception:
        log.exception("transcription failed")
        heard = None
    if not heard:
        return _api_messages(["🎙️ Sorry, I couldn't make out that voice note. Could you type it, or record a "
                              "shorter one (under a minute)?"])
    heard_lang = i18n.analyze(heard)[0] or chat.wants_voice(key)[1]
    echo = "🎙️ " + i18n.translate(["I heard:"], heard_lang)[0] + f" “{heard}”"
    return _web_turn(key, chat.inbound(key, "web", heard), msg.wait, before=[echo], force=True)


class OptIn(BaseModel):
    phone: str
    consent: bool = False


@app.post("/api/optin")
def api_optin(body: OptIn, request: Request):
    """Landing-page opt-in: FarmPartner sends the approved welcome template to this WhatsApp number.
    Falls back to a wa.me link (the farmer messages first) while the template is not approved."""
    phone = "+" + re.sub(r"\D", "", body.phone)
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise HTTPException(400, "Enter your WhatsApp number with country code.")
    if not body.consent:
        raise HTTPException(400, "Please tick the box to agree to receive WhatsApp messages.")
    fallback = {"sent": False, "wa_url": landing.whatsapp_url()}
    if messaging.welcome_status() != "approved":
        return fallback
    if not ratelimit.allow("optin-ip:" + ratelimit.client_ip(request), ratelimit.OPTIN_PER_IP):
        raise HTTPException(429, "Too many requests — please try again later.")
    to = "whatsapp:" + phone
    with session() as db:
        fm = service.get_or_create_farmer(db, to, "whatsapp")
        if (fm.constraints or {}).get("opted_out"):
            return fallback  # they replied STOP; only they can restart the conversation
        fm.constraints = {**(fm.constraints or {}), "opted_in_at": dt.datetime.now(dt.UTC).isoformat()}
        db.commit()
    if not ratelimit.allow("optin:" + phone, ratelimit.OPTIN_PER_PHONE):
        return {"sent": True, "again": True}  # already sent today: don't message them twice
    if not messaging.send_welcome(to):
        return fallback
    return {"sent": True}


@app.get("/api/poll")
def api_poll(phone: str):
    key = "web:" + phone
    with _lock:
        msgs, audio = _outbox.pop(key, []), _audio.pop(key, [])
        return _api_messages(msgs, pending=_pending.get(key, 0) > 0, audio=audio)


def _site_ctx() -> dict:
    """Values shared by the public pages (landing and What & How)."""
    return {"repo": landing.REPO_URL, "whatsapp": landing.whatsapp_url(), "wa": landing.whatsapp_copy(),
            "sprout_path": landing.SPROUT_PATH, "whatsapp_path": landing.WHATSAPP_PATH,
            "video_id": landing.VIDEO_ID, "langs": _lang_counts(), "site_langs": site_i18n.LANGS}


def _lang_counts() -> dict:
    """Languages FarmPartner handles, straight from the code so the website never overclaims."""
    return {"text": len(i18n.LANGS), "voice_in": len(media.SPEECH_CODES), "voice_out": len(voice.TTS_CODES)}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "landing.html", _site_ctx())


@app.get("/what-and-how", response_class=HTMLResponse)
def what_and_how(request: Request):
    return templates.TemplateResponse(request, "what_how.html", {**_site_ctx(), "active": "what-how"})


@app.get("/limits", response_class=HTMLResponse)
def limits_page(request: Request):
    return templates.TemplateResponse(request, "limits.html", _site_ctx())


@app.get("/data-and-science", response_class=HTMLResponse)
def data_page(request: Request):
    return templates.TemplateResponse(request, "data.html", {**_site_ctx(), "active": "data", "stats": sources.STATS,
                                                             "sections": sources.SECTIONS, "steps": sources.STEPS,
                                                             "limits": sources.LIMITS})


@app.get("/data")
def data_short():
    return RedirectResponse("/data-and-science", status_code=301)


@app.get("/team", response_class=HTMLResponse)
def team_page(request: Request):
    return templates.TemplateResponse(request, "team.html", {**_site_ctx(), "active": "team", "team": team.members()})


@app.get("/contact", response_class=HTMLResponse)
def contact_page(request: Request):
    return templates.TemplateResponse(request, "contact.html", {**_site_ctx(), "active": "contact", "email": landing.CONTACT_EMAIL})


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {**_site_ctx(), "active": "privacy", "email": landing.CONTACT_EMAIL})


@app.get("/chat", response_class=HTMLResponse)
def simulator(request: Request):
    return templates.TemplateResponse(request, "simulator.html", {})


@app.get("/sample")
def sample_report():
    """Stable link to the demo farm's field report (built with POST /admin/sample)."""
    with session() as db:
        fm = db.scalar(select(Farmer).where(Farmer.phone == landing.SAMPLE_PHONE))
        fld = service.main_field(fm) if fm else None
        if not fld:
            raise HTTPException(404, "Sample report not built yet")
        return RedirectResponse(f"/r/{fld.public_id}", status_code=302)


@app.post("/admin/sample")
def build_sample(token: str = Form("")):
    """(Re)build the demo farm behind /sample: Garden City, KS, dryland, goal soil health. Admin-token protected."""
    if token != os.getenv("ADMIN_TOKEN", "") or not token:
        raise HTTPException(403)
    s = landing.SAMPLE_FIELD
    with session() as db:
        fm = service.get_or_create_farmer(db, landing.SAMPLE_PHONE, "sample")
        for f in list(fm.fields):
            db.delete(f)
        fm.state, fm.priorities = "ready", s["priorities"]
        db.commit()
        from app.data.geocode import county_name

        fld = Field(farmer_id=fm.id, lat=s["lat"], lon=s["lon"], irrigated=s["irrigated"],
                    place=county_name(s["lat"], s["lon"]) or "Demo field")
        db.add(fld)
        db.commit()
        service.analyze_field(db, fld, with_ndvi=True)
        service.recommend(db, fm, fld)
        threading.Thread(target=service.wait_for_satellite, args=(fld.id,), daemon=True).start()
        return {"report": service.report_url(fld)}


def _field_by_public(public_id: str) -> Field:
    with session() as db:
        fld = db.scalar(select(Field).where(Field.public_id == public_id))
        if not fld:
            raise HTTPException(404)
        _ = fld.recommendations, fld.observations, fld.history, fld.farmer  # load before session closes
        return fld


@app.get("/api/fields/{public_id}")
def field_json(public_id: str):
    fld = _field_by_public(public_id)
    rec = fld.recommendations[-1].result if fld.recommendations else None
    return JSONResponse({"place": fld.place, "lat": fld.lat, "lon": fld.lon, "irrigated": fld.irrigated,
                         "current_rotation": fld.current_rotation, "features": fld.features, "recommendation": rec})


@app.get("/r/{public_id}", response_class=HTMLResponse)
def report(request: Request, public_id: str):
    fld = _field_by_public(public_id)
    rec = fld.recommendations[-1].result if fld.recommendations else None
    f = fld.features or {}
    return templates.TemplateResponse(request, "report.html", {
        "field": fld, "f": f, "c": f.get("climate") or {}, "rec": rec, "labels": SCORE_LABELS,
        "evidence": evidence(f), "gaps": gaps(f),
        "goals": ", ".join(PRIORITY_LABELS.get(p, p) for p in (rec or {}).get("priorities", [])) or "balanced",
        "short_name": short_name, "crop_name": crop_name, "rotation_label": rotation_label, "inch": inch, "disclaimer": DISCLAIMER,
        "observations": fld.observations[-10:],
        "v": report_view.build(fld, rec, f), "updated": _report_date(f.get("built_at")),
        "whatsapp": landing.whatsapp_url(), "whatsapp_path": landing.WHATSAPP_PATH,
    })


def _report_date(built_at) -> str:
    try:
        d = dt.datetime.fromisoformat(str(built_at)) if built_at else dt.datetime.now(dt.timezone.utc)
    except ValueError:
        d = dt.datetime.now(dt.timezone.utc)
    return d.strftime("%b %-d, %Y")


def _send_alerts(db) -> int:
    sent = 0
    for fm in db.scalars(select(Farmer).where(Farmer.state == "ready")):
        fld = service.main_field(fm)
        if not fld or fm.phone.startswith("sample:") or (fm.constraints or {}).get("opted_out"):
            continue
        for body in service.check_alerts(db, fld):
            if not fm.phone.startswith(("web:", "cli:")):
                try:
                    messaging.send(fm.phone, body)
                except Exception:  # e.g. WhatsApp's 24 h session window closed; keep going
                    log.exception("alert to %s failed", fm.phone)
            sent += 1
    return sent


def _refresh_stale_fields(db, max_fields: int = 10, older_than_days: int = 7) -> int:
    """Re-run the NASA/USDA analysis for fields whose snapshot is older than a week (oldest first)."""
    import datetime as _dt

    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=older_than_days)
    stale = db.scalars(select(Field).where((Field.features_at == None) | (Field.features_at < cutoff))  # noqa: E711
                       .order_by(Field.features_at.asc().nullsfirst()).limit(max_fields)).all()
    done = 0
    for fld in stale:
        try:
            service.analyze_field(db, fld, with_ndvi=True)
            service.request_satellite(db, fld)
            done += 1
        except Exception:
            log.exception("refresh failed for field %s", fld.id)
    return done


def _collect_satellite(db) -> int:
    n = 0
    for fld in db.scalars(select(Field)):
        if service.satellite_pending(fld.features or {}):
            try:
                if service.collect_satellite(db, fld) == "done":
                    n += 1
            except Exception:
                log.exception("AppEEARS collect failed for field %s", fld.id)
    return n


def _authorized_job(request: Request, token: str) -> bool:
    if token and token == os.getenv("ADMIN_TOKEN", ""):
        return True
    auth = request.headers.get("authorization", "")
    if not (auth.startswith("Bearer ") and config.SCHEDULER_SA_EMAIL and config.SCHEDULER_AUDIENCE):
        return False
    try:
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(auth[7:], g_requests.Request(), audience=config.SCHEDULER_AUDIENCE)
    except Exception:
        log.warning("rejected scheduler token", exc_info=True)
        return False
    return claims.get("email") == config.SCHEDULER_SA_EMAIL and claims.get("email_verified", False)


@app.post("/internal/daily")
def daily_job(request: Request, token: str = Form("")):
    """Daily: frost/heat/irrigation alerts, then refresh week-old field snapshots. Called by Cloud Scheduler
    (Google-signed OIDC token) or manually with the admin token."""
    if not _authorized_job(request, token):
        raise HTTPException(403)
    with session() as db:
        alerts = _send_alerts(db)
        refreshed = _refresh_stale_fields(db)
        satellite = _collect_satellite(db)
    log.info("daily job: %s alerts, %s fields refreshed, %s satellite results collected", alerts, refreshed, satellite)
    return {"alerts": alerts, "refreshed": refreshed, "satellite_collected": satellite}


@app.post("/admin/alerts/run")
def run_alerts(token: str = Form("")):
    if token != os.getenv("ADMIN_TOKEN", "") or not token:
        raise HTTPException(403)
    with session() as db:
        return {"alerts": _send_alerts(db)}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(config.ROOT / "app" / "static" / "brand" / "favicon-32.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/health")
@app.get("/healthz")  # note: Cloud Run reserves paths ending in "z"; use /health there
def healthz():
    return {"ok": True, "llm": config.llm_enabled(), "twilio": bool(config.TWILIO_ACCOUNT_SID)}

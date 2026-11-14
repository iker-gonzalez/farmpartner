import logging
import os
import threading

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select

from app import chat, config, landing, messaging, ratelimit, service
from app.db import Farmer, Field, init_db, session
from app.engine.explain import DISCLAIMER, SCORE_LABELS, evidence, gaps, inch, short_name
from app.engine.scoring import PRIORITY_LABELS
from app.engine.features import crop_name, rotation_label

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farmpartner")
app = FastAPI(title="FarmPartner")
templates = Jinja2Templates(directory=str(config.ROOT / "app" / "templates"))
app.mount("/static", StaticFiles(directory=str(config.ROOT / "app" / "static")), name="static")
init_db()


def _twiml(messages: list[str]) -> Response:
    from twilio.twiml.messaging_response import MessagingResponse

    r = MessagingResponse()
    for m in messages:
        r.message(m)
    return Response(str(r), media_type="application/xml")


def _run_later(to: str, later):
    try:
        for m in later():
            messaging.send(to, m)
    except Exception:
        log.exception("background job failed")
        messaging.send(to, "Sorry — something went wrong fetching your data. Please try again in a minute.")


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
    turn = chat.inbound(sender, channel, form.get("Body", ""), lat, lon, form.get("MediaUrl0"))
    if turn.later:
        background.add_task(_run_later, sender, turn.later)
    return _twiml(turn.now)


class ChatIn(BaseModel):
    phone: str
    text: str = ""
    lat: float | None = None
    lon: float | None = None
    wait: bool = True  # False: return the immediate reply now and deliver slow follow-ups via /api/poll


_outbox: dict[str, list[str]] = {}
_pending: dict[str, int] = {}
_lock = threading.Lock()


def _deliver_later(key: str, later):
    try:
        msgs = later()
    except Exception:
        log.exception("background job failed")
        msgs = ["Sorry — something went wrong fetching your data. Please try again in a minute."]
    with _lock:
        _outbox.setdefault(key, []).extend(msgs)
        _pending[key] -= 1


@app.post("/api/chat")
def api_chat(msg: ChatIn, request: Request):
    """Chat for the web simulator, tests and demos without WhatsApp."""
    if len(msg.phone) > 64 or len(msg.text) > 1000:
        raise HTTPException(400, "message too long")
    if not (ratelimit.allow("ip:" + ratelimit.client_ip(request), ratelimit.API_PER_IP)
            and ratelimit.allow("ph:" + msg.phone, ratelimit.API_PER_PHONE)):
        raise HTTPException(429, "Too many messages — please wait a minute.")
    key = "web:" + msg.phone
    turn = chat.inbound(key, "web", msg.text, msg.lat, msg.lon)
    out = list(turn.now)
    if turn.later and msg.wait:
        out += turn.later()
    elif turn.later:
        with _lock:
            _pending[key] = _pending.get(key, 0) + 1
        threading.Thread(target=_deliver_later, args=(key, turn.later), daemon=True).start()
    return {"messages": out, "pending": _pending.get(key, 0) > 0}


@app.get("/api/poll")
def api_poll(phone: str):
    key = "web:" + phone
    with _lock:
        msgs = _outbox.pop(key, [])
        return {"messages": msgs, "pending": _pending.get(key, 0) > 0}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "landing.html", {
        "repo": landing.REPO_URL, "whatsapp": landing.whatsapp_url(), "wa": landing.whatsapp_copy(),
        "sprout_path": landing.SPROUT_PATH, "whatsapp_path": landing.WHATSAPP_PATH})


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
    })


def _send_alerts(db) -> int:
    sent = 0
    for fm in db.scalars(select(Farmer).where(Farmer.state == "ready")):
        fld = service.main_field(fm)
        if not fld or fm.phone.startswith("sample:"):
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
            done += 1
        except Exception:
            log.exception("refresh failed for field %s", fld.id)
    return done


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
    log.info("daily job: %s alerts, %s fields refreshed", alerts, refreshed)
    return {"alerts": alerts, "refreshed": refreshed}


@app.post("/admin/alerts/run")
def run_alerts(token: str = Form("")):
    if token != os.getenv("ADMIN_TOKEN", "") or not token:
        raise HTTPException(403)
    with session() as db:
        return {"alerts": _send_alerts(db)}


@app.get("/health")
@app.get("/healthz")  # note: Cloud Run reserves paths ending in "z"; use /health there
def healthz():
    return {"ok": True, "llm": config.llm_enabled(), "twilio": bool(config.TWILIO_ACCOUNT_SID)}

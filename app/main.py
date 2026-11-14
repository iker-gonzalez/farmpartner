import logging
import os
import threading

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select

from app import chat, config, messaging, service
from app.db import Farmer, Field, init_db, session
from app.engine.explain import DISCLAIMER, SCORE_LABELS, evidence, gaps, inch, short_name
from app.engine.scoring import PRIORITY_LABELS
from app.engine.features import crop_name, rotation_label

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farmpartner")
app = FastAPI(title="FarmPartner")
templates = Jinja2Templates(directory=str(config.ROOT / "app" / "templates"))
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
def api_chat(msg: ChatIn):
    """Chat for the web simulator, tests and demos without WhatsApp."""
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
def simulator(request: Request):
    return templates.TemplateResponse(request, "simulator.html", {})


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


@app.post("/admin/alerts/run")
def run_alerts(token: str = Form("")):
    if token != os.getenv("ADMIN_TOKEN", "") or not token:
        raise HTTPException(403)
    sent = 0
    with session() as db:
        for fm in db.scalars(select(Farmer).where(Farmer.state == "ready")):
            fld = service.main_field(fm)
            if not fld:
                continue
            for body in service.check_alerts(db, fld):
                if not fm.phone.startswith(("web:", "cli:")):
                    messaging.send(fm.phone, body)
                sent += 1
    return {"alerts": sent}


@app.get("/health")
@app.get("/healthz")  # note: Cloud Run reserves paths ending in "z"; use /health there
def healthz():
    return {"ok": True, "llm": config.llm_enabled(), "twilio": bool(config.TWILIO_ACCOUNT_SID)}

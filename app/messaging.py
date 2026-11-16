"""Outbound messages through Twilio (WhatsApp or SMS). Logs instead when Twilio is not configured.

Prompts with buttons/lists (app/prompts.py) go to WhatsApp as Twilio Content API quick-reply / list-picker
templates. Session messages need no WhatsApp approval. If anything fails (or on SMS) the same prompt is sent as
plain text with the options listed, so the conversation never breaks.
"""
import hashlib
import json
import logging
import threading
import time
from contextlib import contextmanager

import httpx

from app import config
from app.prompts import as_text, is_prompt

log = logging.getLogger(__name__)
MAX_SMS = 1500  # Twilio limit is 1600 chars
CONTENT_API = "https://content.twilio.com/v1/Content"
_content_sids: dict[str, str] = {}
_approval = {"status": None, "checked": 0.0}


def split_message(body: str, limit: int = MAX_SMS) -> list[str]:
    """Split a long message for Twilio without ever cutting a word or a link in half: break before a section
    heading (a line starting with *bold*) when one is in reach, else at a line break, else at a space."""
    parts, rest = [], body
    while len(rest) > limit:
        window = rest[:limit + 1]
        cut = window.rfind("\n*")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 4:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit          # one unbroken run longer than the limit: nothing better to do
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n ") if cut < limit or rest[cut:cut + 1] in "\n " else rest[cut:]
    if rest.strip():
        parts.append(rest)
    return parts


def _client():
    from twilio.rest import Client

    return Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def _content_sid(prompt: dict) -> str:
    """Get or create a Content API template for this prompt's choices; the prompt text is variable {{1}}."""
    shape = {k: v for k, v in prompt.items() if k != "text"}
    name = "farmpartner_" + hashlib.sha1(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:12]
    if name in _content_sids:
        return _content_sids[name]
    auth = (config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    url = CONTENT_API + "?PageSize=500"
    while url:  # reuse a template created by an earlier process
        page = httpx.get(url, auth=auth, timeout=30).raise_for_status().json()
        for c in page.get("contents", []):
            if c.get("friendly_name") == name:
                _content_sids[name] = c["sid"]
                return c["sid"]
        url = (page.get("meta") or {}).get("next_page_url")
    if "buttons" in prompt:
        types = {"twilio/quick-reply": {"body": "{{1}}", "actions": [{"title": b["title"], "id": b["id"]}
                                                                       for b in prompt["buttons"]]}}
    else:
        lst = prompt["list"]
        types = {"twilio/list-picker": {"body": "{{1}}", "button": lst["button"],
                                        "items": [{"item": i["title"], "id": i["id"], "description": i["description"]}
                                                  for i in lst["items"]]}}
    types["twilio/text"] = {"body": "{{1}}"}
    body = {"friendly_name": name, "language": "en", "variables": {"1": "Choose an option"}, "types": types}
    sid = httpx.post(CONTENT_API, auth=auth, json=body, timeout=30).raise_for_status().json()["sid"]
    _content_sids[name] = sid
    return sid


def send(to: str, message) -> None:
    """message: plain string or a prompt dict (app/prompts.py)."""
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN):
        log.warning("Twilio not configured; would send to %s:\n%s", to, as_text(message))
        return
    client = _client()
    whatsapp = to.startswith("whatsapp:")
    sender = config.TWILIO_WHATSAPP_FROM if whatsapp else config.TWILIO_SMS_FROM
    if is_prompt(message) and whatsapp:
        try:
            client.messages.create(from_=sender, to=to, content_sid=_content_sid(message),
                                   content_variables=json.dumps({"1": message["text"]}))
            return
        except Exception:
            log.warning("interactive message failed; falling back to text", exc_info=True)
    body = as_text(message)
    for part in split_message(body):
        client.messages.create(from_=sender, to=to, body=part)


def welcome_status() -> str:
    """WhatsApp approval status of the welcome template ("approved", "received", "pending", "rejected", ...),
    cached for 5 minutes. "unconfigured" when there is no template or no Twilio account."""
    if not (config.WELCOME_CONTENT_SID and config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN):
        return "unconfigured"
    if _approval["status"] == "approved" or time.time() - _approval["checked"] < 300:
        return _approval["status"] or "unknown"
    try:
        r = httpx.get(f"{CONTENT_API}/{config.WELCOME_CONTENT_SID}/ApprovalRequests",
                      auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN), timeout=15)
        _approval["status"] = (r.raise_for_status().json().get("whatsapp") or {}).get("status") or "unknown"
    except Exception:
        log.warning("could not read welcome template status", exc_info=True)
        _approval["status"] = _approval["status"] or "unknown"
    _approval["checked"] = time.time()
    return _approval["status"]


def send_welcome(to: str) -> bool:
    """Business-initiated first message (approved template with Get started / Not now). False if not possible."""
    if welcome_status() != "approved":
        return False
    try:
        _client().messages.create(from_=config.TWILIO_WHATSAPP_FROM, to=to, content_sid=config.WELCOME_CONTENT_SID)
        return True
    except Exception:
        log.warning("welcome template to %s failed", to, exc_info=True)
        return False


TYPING_API = "https://messaging.twilio.com/v3/Indicators/Typing.json"


def typing(message_sid: str | None) -> None:
    """Show WhatsApp's "typing…" bubble to the farmer (Twilio public beta). It also marks their message as read and
    lasts up to 25 s or until our next message arrives. Best effort: failures are only logged."""
    if not (message_sid and config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN):
        return
    try:
        httpx.post(TYPING_API, auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN), timeout=5,
                   json={"channel": "WHATSAPP", "messageId": message_sid}).raise_for_status()
    except Exception as e:
        log.info("typing indicator skipped: %s", e)


@contextmanager
def typing_while(message_sid: str | None, every_s: float = 20, max_s: float = 180, first_after_s: float = 2):
    """Keep "typing…" visible while slow work runs (refreshed before WhatsApp's 25 s timeout). The first indicator
    waits a moment so the instant reply (e.g. "📡 Got your pin!") lands first; its arrival would clear the bubble."""
    if not message_sid:
        yield
        return
    stop = threading.Event()

    def loop():
        start = time.monotonic()
        if stop.wait(first_after_s):
            return
        while not stop.is_set() and time.monotonic() - start < max_s:
            typing(message_sid)
            stop.wait(every_s)

    threading.Thread(target=loop, daemon=True).start()
    try:
        yield
    finally:
        stop.set()


def send_media(to: str, url: str) -> None:
    """A media message (e.g. a voice note) on WhatsApp; Twilio fetches the file from url."""
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN) or not to.startswith("whatsapp:"):
        log.warning("would send media to %s: %s", to, url)
        return
    _client().messages.create(from_=config.TWILIO_WHATSAPP_FROM, to=to, media_url=[url])

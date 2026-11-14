"""Spoken replies: FarmPartner's answer also sent as a WhatsApp voice note, in the farmer's language.

When: after the farmer sends a voice note (reply in kind), or always once they ask for audio ("ahotsez",
"en audio", "voice please"); "text only" turns it off. Google Cloud Text-to-Speech (standard voices, OGG/Opus) →
private cache bucket → served at an unguessable /voice/<id>.ogg URL that Twilio fetches.
Cost: TTS standard voices are free for the first ~4M characters a month; each voice note is one extra WhatsApp
message for Twilio (inside the 24 h service window Meta charges nothing).
"""
import base64
import logging
import re
import uuid

import httpx

from app import config

log = logging.getLogger(__name__)
TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
TTS_CODES = {"en": "en-US", "es": "es-ES", "eu": "eu-ES", "ca": "ca-ES", "gl": "gl-ES", "pt": "pt-BR", "fr": "fr-FR",
             "de": "de-DE", "it": "it-IT", "ja": "ja-JP", "zh": "cmn-CN", "ko": "ko-KR", "ru": "ru-RU", "ar": "ar-XA",
             "hi": "hi-IN", "th": "th-TH", "el": "el-GR", "tr": "tr-TR"}
MAX_CHARS = 1400
ID = re.compile(r"[a-f0-9]{32}")
ASK_ON = re.compile(r"\b(audio|audios|voice|voz|ahotsez|audioan|entzun|nota de voz|notas de voz|speak|read it to me|"
                    r"h[aá]blame|habla(?:me)?|en voz alta|out loud|vocal|áudio)\b", re.I)
ASK_OFF = re.compile(r"\b(text only|only text|no audio|no voice|solo texto|sólo texto|sin audio|sin voz|"
                     r"testu bakarrik|audiorik ez|stop audio)\b", re.I)


def asked(text: str) -> bool | None:
    """True/False when the farmer asks to turn spoken replies on/off; None otherwise."""
    t = text or ""
    if len(t.split()) > 12:
        return None
    if ASK_OFF.search(t):
        return False
    if ASK_ON.search(t):
        return True
    return None


def speakable(msgs: list) -> str:
    """The turn's text as something pleasant to hear: no emoji, markdown, links or button lists."""
    parts = []
    for m in msgs:
        t = m["text"] if isinstance(m, dict) else str(m)
        t = re.sub(r"https?://\S+", "", t)
        t = re.sub(r"[*_~`]", "", t)
        t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]", "", t)
        t = re.sub(r"^\s*[•\-]\s*", "", t, flags=re.M)
        parts.append(t.strip())
    text = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        text = cut[:max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "), 200) + 1]
    return text


def synthesize(text: str, lang: str) -> bytes:
    from app.media import _google_headers
    code = TTS_CODES.get(lang or "en", "en-US")
    body = {"input": {"text": text}, "voice": {"languageCode": code, "ssmlGender": "NEUTRAL"},
            "audioConfig": {"audioEncoding": "OGG_OPUS", "speakingRate": 1.0}}
    r = httpx.post(TTS_URL, json=body, headers=_google_headers(), timeout=40)
    if r.status_code >= 400:
        log.warning("text-to-speech %s: %s", r.status_code, r.text[:300])
        r.raise_for_status()
    return base64.b64decode(r.json()["audioContent"])


def store(audio: bytes) -> str:
    """Save the clip; return its public URL (an unguessable id)."""
    vid = uuid.uuid4().hex
    from app.data import http
    bucket = http._gcs_bucket()
    if bucket:
        bucket.blob(f"voice/{vid}.ogg").upload_from_string(audio, content_type="audio/ogg")
    else:
        (config.CACHE_DIR / "voice").mkdir(parents=True, exist_ok=True)
        (config.CACHE_DIR / "voice" / f"{vid}.ogg").write_bytes(audio)
    return f"{config.PUBLIC_BASE_URL}/voice/{vid}.ogg"


def load(vid: str) -> bytes | None:
    if not ID.fullmatch(vid or ""):
        return None
    from app.data import http
    bucket = http._gcs_bucket()
    if bucket:
        blob = bucket.blob(f"voice/{vid}.ogg")
        return blob.download_as_bytes() if blob.exists() else None
    p = config.CACHE_DIR / "voice" / f"{vid}.ogg"
    return p.read_bytes() if p.exists() else None

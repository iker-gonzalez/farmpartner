"""WhatsApp media: voice notes → text (Google Cloud Speech-to-Text), photos → Claude's image input.

Twilio media URLs need the account's credentials. Speech-to-Text uses the runtime service account (Application
Default Credentials), so no extra key is stored.
"""
import base64
import logging

import httpx

from app import config

log = logging.getLogger(__name__)
SPEECH_URL = "https://speech.googleapis.com/v1p1beta1/speech:recognize"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}  # what Claude accepts
MAX_IMAGE_BYTES = 5 * 1024 * 1024
OPUS_RATES = {8000, 12000, 16000, 24000, 48000}


def kind(content_type: str | None) -> str | None:
    ct = (content_type or "").split(";")[0].strip().lower()
    return "audio" if ct.startswith("audio/") else "image" if ct.startswith("image/") else None


def download(url: str) -> tuple[bytes, str]:
    r = httpx.get(url, auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN), follow_redirects=True, timeout=30)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "").split(";")[0].strip().lower()


def _google_headers() -> dict:
    import google.auth
    from google.auth.transport.requests import Request

    creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    if not getattr(creds, "service_account_email", None) and project:
        headers["x-goog-user-project"] = project  # local user credentials need a quota project; Cloud Run's SA does not
    return headers


def _opus_rate(data: bytes) -> int:
    i = data.find(b"OpusHead")
    rate = int.from_bytes(data[i + 12:i + 16], "little") if i >= 0 else 0
    return rate if rate in OPUS_RATES else 16000


SPEECH_CODES = {"en": "en-US", "es": "es-ES", "eu": "eu-ES", "ca": "ca-ES", "gl": "gl-ES", "pt": "pt-BR", "fr": "fr-FR",
                "de": "de-DE", "it": "it-IT", "ja": "ja-JP", "zh": "cmn-Hans-CN", "ko": "ko-KR", "ru": "ru-RU",
                "ar": "ar-SA", "hi": "hi-IN", "th": "th-TH", "el": "el-GR", "tr": "tr-TR", "bn": "bn-IN", "ur": "ur-PK",
                "id": "id-ID", "sw": "sw-KE", "mr": "mr-IN", "te": "te-IN", "ta": "ta-IN", "vi": "vi-VN",
                "pa": "pa-Guru-IN", "gu": "gu-IN", "kn": "kn-IN", "ml": "ml-IN", "fa": "fa-IR", "pl": "pl-PL",
                "uk": "uk-UA", "nl": "nl-NL", "ro": "ro-RO", "tl": "fil-PH", "ms": "ms-MY", "he": "iw-IL",
                "am": "am-ET", "my": "my-MM", "cs": "cs-CZ", "hu": "hu-HU", "sv": "sv-SE", "af": "af-ZA"}


def _wav_pcm(data: bytes) -> tuple[int, bytes] | None:
    """(sample rate, raw 16-bit mono samples) from a WAV file, walking its chunks (some writers add extra ones)."""
    i, rate, fmt_ok = 12, None, False
    while i + 8 <= len(data):
        cid, size = data[i:i + 4], int.from_bytes(data[i + 4:i + 8], "little")
        body = data[i + 8:i + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            fmt_ok = int.from_bytes(body[0:2], "little") == 1 and int.from_bytes(body[2:4], "little") == 1 \
                and int.from_bytes(body[14:16], "little") == 16
            rate = int.from_bytes(body[4:8], "little")
        elif cid == b"data":
            return (rate, body) if fmt_ok and rate else None
        i += 8 + size + (size & 1)
    return None


def transcribe(data: bytes, content_type: str, langs: list[str] | None = None) -> str | None:
    """Transcript of a short voice note (≤ 1 minute) in the first of langs (up to 3 alternatives also heard).
    None if nothing was understood."""
    codes = list(dict.fromkeys(SPEECH_CODES[x] for x in (langs or ["en", "es"]) if x in SPEECH_CODES)) or ["en-US"]
    if "ogg" in content_type or "opus" in content_type:
        audio_cfg = {"encoding": "OGG_OPUS", "sampleRateHertz": _opus_rate(data)}
    elif data[:4] == b"RIFF" and data[8:12] == b"WAVE" and (pcm := _wav_pcm(data)):
        rate, data = pcm
        audio_cfg = {"encoding": "LINEAR16", "sampleRateHertz": rate}
    elif "webm" in content_type:
        audio_cfg = {"encoding": "WEBM_OPUS", "sampleRateHertz": 48000}
    elif "amr" in content_type:
        audio_cfg = {"encoding": "AMR", "sampleRateHertz": 8000}
    else:
        audio_cfg = {}  # let the API detect WAV/FLAC/MP3 headers
        if "mpeg" in content_type or "mp3" in content_type:
            audio_cfg = {"encoding": "MP3", "sampleRateHertz": 16000}
    body = {"config": {**audio_cfg, "languageCode": codes[0], "alternativeLanguageCodes": codes[1:4],
                       "enableAutomaticPunctuation": True},
            "audio": {"content": base64.b64encode(data).decode()}}
    r = httpx.post(SPEECH_URL, json=body, headers=_google_headers(), timeout=40)
    if r.status_code >= 400:
        log.warning("speech-to-text %s: %s", r.status_code, r.text[:300])
        r.raise_for_status()
    parts = [res["alternatives"][0]["transcript"].strip() for res in r.json().get("results", []) if res.get("alternatives")]
    if not parts:
        log.warning("speech-to-text heard nothing (%s, %d bytes, %s)", content_type, len(data), codes[:4])
    return " ".join(p for p in parts if p) or None


def image_block(data: bytes, content_type: str) -> dict | None:
    if content_type not in IMAGE_TYPES or len(data) > MAX_IMAGE_BYTES:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": content_type,
                                        "data": base64.b64encode(data).decode()}}

"""Spanish for the scripted onboarding messages. Claude's own answers already follow the farmer's language.

detect() guesses the language of what the farmer writes; translate() turns FarmPartner's fixed messages (and the
titles of tap-buttons and list items, never their ids) into Spanish with Claude, cached per string, so each
prompt is translated once. If Claude is unavailable, the English original is sent.
"""
import json
import logging
import re
import threading

from app import config
from app.prompts import is_prompt

log = logging.getLogger(__name__)
_cache: dict[str, str] = {}
_lock = threading.Lock()

ES_WORDS = {"hola", "quiero", "gracias", "buenos", "buenas", "tengo", "mi", "mis", "campo", "riego", "secano",
            "plantar", "cultivo", "cultivos", "puedo", "por", "favor", "que", "qué", "como", "cómo", "cuándo", "cuando",
            "debo", "regar", "esta", "semana", "el", "la", "los", "las", "del", "es", "está", "son", "una", "un", "con",
            "para", "pero", "también", "agua", "suelo", "lluvia", "helada", "sí", "vale", "ayuda", "dónde", "donde"}
EN_WORDS = {"hi", "hello", "the", "my", "what", "how", "is", "should", "i", "water", "field", "crops", "yes", "frost",
            "please", "thanks", "this", "week", "do", "can", "you", "it", "rain", "grow", "help", "where", "when"}
ES_SWITCH = re.compile(r"\b(español|espanol|castellano|spanish|en español)\b", re.I)
EN_SWITCH = re.compile(r"\b(english|in english|inglés|ingles)\b", re.I)


def detect(text: str) -> str | None:
    """'es', 'en' or None when the message is too short or ambiguous to tell (e.g. "corn", "2")."""
    t = (text or "").strip()
    if not t:
        return None
    if ES_SWITCH.search(t):
        return "es"
    if EN_SWITCH.search(t):
        return "en"
    if re.search(r"[ñ¿¡]", t):
        return "es"
    words = re.findall(r"[a-záéíóúñü]+", t.lower())
    es, en = sum(w in ES_WORDS for w in words), sum(w in EN_WORDS for w in words)
    if es >= 2 and es > en:
        return "es"
    if en >= 2 and en > es:
        return "en"
    return None


def is_switch(text: str) -> bool:
    """A message that only asks to change language ("en castellano", "in English please")."""
    t = re.sub(r"\b(en|in|por favor|please|habla|hablame|háblame|speak)\b", "", (text or "").lower())
    t = ES_SWITCH.sub("", EN_SWITCH.sub("", t))
    return bool(ES_SWITCH.search(text or "") or EN_SWITCH.search(text or "")) and not re.search(r"[a-z]{3,}", t)


def _strings(msg) -> list[str]:
    if not is_prompt(msg):
        return [msg]
    out = [msg["text"]]
    if "buttons" in msg:
        out += [b["title"] for b in msg["buttons"]]
    else:
        out += [msg["list"]["button"]] + [x for it in msg["list"]["items"] for x in (it["title"], it["description"])]
    return out


def _rebuild(msg, tr: dict[str, str]):
    if not is_prompt(msg):
        return tr.get(msg, msg)
    m = json.loads(json.dumps(msg))
    m["text"] = tr.get(m["text"], m["text"]).replace("\n", " ")
    if "buttons" in m:
        for b in m["buttons"]:
            b["title"] = tr.get(b["title"], b["title"])[:20]
    else:
        m["list"]["button"] = tr.get(m["list"]["button"], m["list"]["button"])[:20]
        for it in m["list"]["items"]:
            it["title"] = tr.get(it["title"], it["title"])[:24]
            it["description"] = tr.get(it["description"], it["description"])[:72]
    return m


SYSTEM = ("You translate WhatsApp messages from FarmPartner, a farm advisor, into clear, friendly Spanish for "
          "farmers (use 'usted'). Keep every emoji, *bold* marker, line break, URL, number, unit (in, °F, in/yr) and "
          "proper name exactly. Keep 'FarmPartner', 'NASA', 'USDA', 'RESET', 'REPORT' and 'HELP' as they are. "
          "Very short strings are button labels: keep them short (max 20 characters). Return ONLY a JSON array of "
          "strings with the same length and order as the input.")


def _translate_batch(texts: list[str]) -> list[str]:
    from app.agent import claude_client

    resp = claude_client().beta.messages.create(
        model=config.ANTHROPIC_MODEL, max_tokens=8000, system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(texts, ensure_ascii=False)}],
        output_config={"effort": "low"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
    out = json.loads(raw)
    if not (isinstance(out, list) and len(out) == len(texts) and all(isinstance(x, str) for x in out)):
        raise ValueError("translation shape mismatch")
    return out


def translate(msgs: list, lang: str) -> list:
    if lang != "es" or not msgs or not config.llm_enabled():
        return msgs
    wanted = list(dict.fromkeys(s for m in msgs for s in _strings(m) if s and s.strip()))
    with _lock:
        missing = [s for s in wanted if s not in _cache]
    if missing:
        try:
            for src, dst in zip(missing, _translate_batch(missing)):
                with _lock:
                    _cache[src] = dst
        except Exception:
            log.exception("translation failed; sending English")
            return msgs
    with _lock:
        tr = {s: _cache[s] for s in wanted if s in _cache}
    return [_rebuild(m, tr) for m in msgs]

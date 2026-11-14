"""The farmer's language for the scripted onboarding messages. Claude's own answers already follow it.

detect() guesses the language of what the farmer writes (script for Japanese, Chinese, Korean, Cyrillic, Arabic,
Hindi, Thai, Greek; common words for Spanish, Portuguese, French, German, Italian, English). translate() turns
FarmPartner's fixed messages (and the titles of tap-buttons and list items, never their ids) into that language
with Claude, cached per string, so each prompt is translated once. If Claude is unavailable, English is sent.
"""
import json
import logging
import re
import threading

from app import config
from app.prompts import is_prompt

log = logging.getLogger(__name__)
_cache: dict[tuple[str, str], str] = {}
_lock = threading.Lock()
_loaded: set[str] = set()


CACHE_VERSION = 4  # bump when the glossary or prompt changes, so cached translations are redone


def _store_name(lang: str) -> str:
    return f"translations-v{CACHE_VERSION}-{lang}.json"


def _load(lang: str) -> None:
    """Translations survive restarts: one JSON file per language in the cache bucket (or local cache dir)."""
    if lang in _loaded:
        return
    from app.data import http
    text = None
    p = http.CACHE_DIR / _store_name(lang)
    if p.exists():
        text = p.read_text()
    else:
        text = http._gcs_get(_store_name(lang), 0)
    with _lock:
        if text:
            for k, v in json.loads(text).items():
                _cache.setdefault((lang, k), v)
        _loaded.add(lang)


def _save(lang: str) -> None:
    from app.data import http
    with _lock:
        text = json.dumps({k: v for (lg, k), v in _cache.items() if lg == lang}, ensure_ascii=False)
    try:
        http.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (http.CACHE_DIR / _store_name(lang)).write_text(text)
        http._gcs_put(_store_name(lang), 0, text)
    except Exception:
        log.warning("could not save translations", exc_info=True)

LANGS = {"es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German", "it": "Italian", "ja": "Japanese",
         "zh": "Chinese (Simplified)", "ko": "Korean", "ru": "Russian", "ar": "Arabic", "hi": "Hindi", "th": "Thai",
         "el": "Greek", "eu": "Basque (Euskara)", "ca": "Catalan", "gl": "Galician", "en": "English"}
WORDS = {
    "es": {"hola", "quiero", "gracias", "buenos", "buenas", "tengo", "mi", "mis", "campo", "riego", "secano", "plantar",
           "cultivo", "cultivos", "puedo", "por", "favor", "que", "qué", "como", "cómo", "cuándo", "cuando", "debo",
           "regar", "esta", "semana", "el", "la", "los", "las", "del", "es", "está", "son", "una", "un", "con", "para",
           "pero", "también", "agua", "suelo", "lluvia", "helada", "sí", "vale", "ayuda", "dónde", "donde", "y"},
    "pt": {"olá", "ola", "obrigado", "obrigada", "quero", "meu", "minha", "não", "sim", "você", "está", "plantação",
           "fazenda", "chuva", "água", "solo", "posso", "devo", "regar", "semana", "uma", "com", "também", "onde"},
    "fr": {"bonjour", "merci", "je", "veux", "mon", "mes", "champ", "est", "les", "des", "une", "pour", "avec", "oui",
           "quoi", "comment", "planter", "pluie", "eau", "sol", "dois", "arroser", "semaine", "ferme", "où"},
    "de": {"hallo", "danke", "ich", "möchte", "mein", "meine", "feld", "ist", "und", "nicht", "ja", "bitte", "wie",
           "was", "pflanzen", "regen", "wasser", "boden", "soll", "woche", "hof", "wo"},
    "it": {"ciao", "grazie", "voglio", "mio", "mia", "campo", "è", "sono", "piantare", "come", "cosa", "sì", "pioggia",
           "acqua", "suolo", "devo", "irrigare", "settimana", "azienda", "dove"},
    "eu": {"kaixo", "eskerrik", "asko", "euskaraz", "euskeraz", "nire", "nahi", "dut", "ditut", "zer", "nola", "bai",
           "ez", "da", "dira", "eta", "baina", "lurra", "ura", "euria", "landatu", "soroa", "baserria", "egun", "on",
           "hitz", "egin", "egingo", "zenidake", "ditzakezu", "mintzatu", "gaztelaniaz"},
    "ca": {"hola", "gràcies", "vull", "meu", "meva", "camp", "és", "són", "amb", "però", "també", "aigua", "sòl",
           "pluja", "plantar", "puc", "he", "regar", "setmana", "què", "com", "on", "català"},
    "gl": {"ola", "grazas", "quero", "meu", "miña", "campo", "é", "son", "con", "pero", "tamén", "auga", "chan",
           "chuvia", "plantar", "podo", "debo", "regar", "semana", "que", "como", "onde", "galego", "non"},
    "en": {"hi", "hello", "the", "my", "what", "how", "is", "should", "i", "water", "field", "crops", "yes", "frost",
           "please", "thanks", "this", "week", "do", "can", "you", "it", "rain", "grow", "help", "where", "when", "want"},
}
SCRIPTS = [("ja", r"[\u3040-\u30ff]"), ("ko", r"[\uac00-\ud7af]"), ("zh", r"[\u4e00-\u9fff]"),
           ("ru", r"[\u0400-\u04ff]"), ("ar", r"[\u0600-\u06ff]"), ("hi", r"[\u0900-\u097f]"),
           ("th", r"[\u0e00-\u0e7f]"), ("el", r"[\u0370-\u03ff]")]
SWITCH = [("es", r"\b(español|espanol|castellano|spanish)\b"), ("pt", r"\b(português|portugues|portuguese)\b"),
          ("fr", r"\b(français|francais|french)\b"), ("de", r"\b(deutsch|german)\b"),
          ("it", r"\b(italiano|italian)\b"), ("ja", r"(日本語|\bjapanese\b)"),
          ("eu", r"\b(euskara|euskaraz|euskera|euskeraz|basque|vasco|euskaldun)\b"),
          ("ca", r"\b(català|catala|catalan|catalán)\b"), ("gl", r"\b(galego|galician|gallego)\b"), ("en", r"\b(english|inglés|ingles)\b")]


def detect(text: str) -> str | None:
    """A language code, or None when the message is too short or ambiguous to tell (e.g. "corn", "2")."""
    t = (text or "").strip()
    if not t:
        return None
    for code, pat in SWITCH:
        if re.search(pat, t, re.I):
            return code
    for code, pat in SCRIPTS:
        if re.search(pat, t):
            return code
    if re.search(r"[ñ¿¡]", t):
        return "es"
    first = re.sub(r"[^\wáéíóúñü]", "", t.lower().split()[0]) if t.split() else ""
    greeting = {"hola": "es", "buenas": "es", "olá": "pt", "oi": "pt", "bonjour": "fr", "salut": "fr", "ciao": "it",
                "hallo": "de", "kaixo": "eu", "bon": "ca"}.get(first)
    if greeting and len(t.split()) <= 3:
        return greeting
    words = re.findall(r"[a-záàâãéèêíóôõúüçñäöß]+", t.lower())
    scores = sorted(((sum(w in ws for w in words), code) for code, ws in WORDS.items()), reverse=True)
    (best, code), (second, _) = scores[0], scores[1]
    return code if best >= 2 and best > second else None


def explicit_language(text: str) -> str | None:
    """The language a short message explicitly asks for ("en español porfa", "contéstame en euskera")."""
    t = text or ""
    if len(t.split()) > 8:
        return None
    for code, pat in SWITCH:
        if re.search(pat, t, re.I):
            return code
    return None


def is_switch(text: str) -> bool:
    """A message that only asks to change language ("en castellano", "in English please", "日本語で")."""
    t = (text or "").lower()
    if not any(re.search(p, t, re.I) for _, p in SWITCH):
        return False
    for _, p in SWITCH:
        t = re.sub(p, "", t, flags=re.I)
    t = re.sub(r"\b(en|in|em|auf|por favor|please|habla|hablame|háblame|hablas|me|speak|parle|sprich|parla|hitz|egin|"
               r"egingo|zenidake|mintzatu|ditzakezu|dezakezu|nahi|dut|parla|parlar|falar|falame|mesedez|si us plau|por favore|"
               r"s'il vous plaît|bitte|can you|could you|puedes|podrías|pode|peux)\b|で|お願い|[^\w\s]", "", t)
    return not re.search(r"[a-zà-ÿ]{3,}", t)


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


SYSTEM = ("You translate WhatsApp messages from FarmPartner, a farm advisor, into clear, friendly {lang} for "
          "farmers (polite form). Keep every emoji, *bold* marker, line break, URL, number, unit (in, °F, in/yr) and "
          "proper name exactly. Keep 'FarmPartner', 'NASA', 'USDA', 'RESET', 'REPORT' and 'HELP' as they are. "
          "Very short strings are button labels: keep them short (max 20 characters). Return ONLY a JSON array of "
          "strings with the same length and order as the input.{glossary}")
GLOSSARY = {
    "es": " Always address the farmer as \"usted\" (never \"tú\"), consistently in every string. Use these farming terms (Spain's usage, understood everywhere): dryland = secano (\"de secano\"), "
          "irrigated = regadío (\"de regadío\"), rain-fed = secano (never \"temporal\"), soybeans = soja, "
          "corn = maíz, elevators/buyers = cooperativas o compradores, ZIP code = código postal, extension agent = "
          "técnico agrícola o agrónomo, cover crop = cubierta vegetal o cultivo de cobertura, fallow = barbecho, orchard = plantación leñosa (never "
          "\"huerto\", which means a vegetable garden), tree crop = cultivo leñoso.",
}


def _translate_batch(texts: list[str], lang: str) -> list[str]:
    from app.agent import quick

    raw = quick(SYSTEM.format(lang=LANGS.get(lang, lang), glossary=GLOSSARY.get(lang, "")),
                json.dumps(texts, ensure_ascii=False), max_tokens=8000)
    raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
    out = json.loads(raw)
    if not (isinstance(out, list) and len(out) == len(texts) and all(isinstance(x, str) for x in out)):
        raise ValueError("translation shape mismatch")
    return out


def translate(msgs: list, lang: str) -> list:
    if lang in (None, "en") or lang not in LANGS or not msgs or not config.llm_enabled():
        return msgs
    wanted = list(dict.fromkeys(s for m in msgs for s in _strings(m) if s and s.strip()))
    try:
        _load(lang)
    except Exception:
        log.warning("could not load saved translations", exc_info=True)
    with _lock:
        missing = [s for s in wanted if (lang, s) not in _cache]
    if missing:
        try:
            for src, dst in zip(missing, _translate_batch(missing, lang)):
                with _lock:
                    _cache[(lang, src)] = dst
            _save(lang)
        except Exception:
            log.exception("translation failed; sending English")
            return msgs
    with _lock:
        tr = {s: _cache[(lang, s)] for s in wanted if (lang, s) in _cache}
    return [_rebuild(m, tr) for m in msgs]


PREWARM_LANGS = ("es", "eu", "pt", "fr", "ja", "de", "it", "zh", "ca")


def prewarm(messages: list, langs=PREWARM_LANGS) -> None:
    """Translate FarmPartner's fixed messages ahead of time, so no farmer waits for a first-time translation."""
    for lang in langs:
        try:
            translate(messages, lang)
        except Exception:
            log.warning("translation prewarm failed for %s", lang, exc_info=True)

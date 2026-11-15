"""Metric units for farmers outside the U.S. FarmPartner's scripted messages are written in inches, °F and acres;
to_metric() rewrites them deterministically (no LLM), so numbers stay exact."""
import re

_NUM = r"([-+]?\d+(?:\.\d+)?)"
METRIC_ASK = re.compile(r"(°\s?c\b|celsius|centígrad|metric|métric|\bmm\b|mil[ií]metros|litros|liters|litres|"
                        r"\bl/yr\b|hect[aá]reas?)", re.I)  # not bare "ha": "se me ha olvidado" is not hectares
IMPERIAL_ASK = re.compile(r"(°\s?f\b|fahrenheit|imperial|inches|pulgadas|acres)", re.I)


def preferred(farmer) -> str:
    """'metric' or 'imperial': the farmer's choice, else imperial for U.S. (+1) numbers and web demos."""
    chosen = (farmer.constraints or {}).get("units")
    if chosen in ("metric", "imperial"):
        return chosen
    phone = farmer.phone or ""
    return "imperial" if phone.startswith(("whatsapp:+1", "+1", "web:", "sample:", "cli:")) else "metric"


def asked(text: str) -> str | None:
    """A request to switch units ("I want °C and mm", "en pulgadas"), or None."""
    t = text or ""
    if len(t.split()) > 14 or re.search(r"\d", t):  # "we got 1.2 inches of rain" is a report, not a request
        return None
    if METRIC_ASK.search(t) and not IMPERIAL_ASK.search(t):
        return "metric"
    if IMPERIAL_ASK.search(t) and not METRIC_ASK.search(t):
        return "imperial"
    return None


def _mm(x: str) -> str:
    v = float(x) * 25.4
    return f"{v:+.0f}" if x.startswith(("+", "-")) else f"{v:.0f}"


def to_metric(text: str) -> str:
    if not isinstance(text, str):
        return text
    t = re.sub(_NUM + r"\s?in/yr", lambda m: f"{_mm(m.group(1))} mm/yr", text)
    t = re.sub(_NUM + r"\s?(?:inches|inch|in)(?=[\s.,;:)!?]|$)", lambda m: f"{_mm(m.group(1))} mm", t)
    t = re.sub(_NUM + r"\s?°F", lambda m: f"{(float(m.group(1)) - 32) * 5 / 9:.0f}°C", t)
    t = re.sub(_NUM + r"\s?acres?\b", lambda m: f"{float(m.group(1)) * 0.4047:.1f} ha", t)
    return t


def convert(msgs: list) -> list:
    out = []
    for m in msgs:
        if isinstance(m, dict):
            m = {**m, "text": to_metric(m["text"])}
        out.append(to_metric(m))
    return out

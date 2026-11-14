"""Conversational layer once the farmer is onboarded.

Claude handles intent, free-text extraction and plain-language answers, but every number comes from a tool:
the rotation engine, NASA-derived features, or the forecast. Without an API key a keyword router keeps the demo working.
"""
import json
import logging
import re

from app import config, service
from app.db import Farmer, Message, session
from app.engine import explain as ex
from app.engine.knowledge import crops, crops_in_text
from app.onboarding import HELP, parse_irrigated, parse_priorities

log = logging.getLogger(__name__)

SYSTEM = """You are FarmPartner, a friendly farm advisor that farmers reach over WhatsApp or SMS. Many farmers are not \
technical. You turn NASA Earth-observation data (POWER weather & soil wetness, MODIS vegetation, GRACE water storage) and \
USDA data (crop maps, soil survey) into practical advice on crop rotations, watering, frost and heat.

Rules:
- Keep replies short for a phone screen: at most 6 short lines, plain words, no tables, no markdown headers. \
WhatsApp *bold* is fine. Use the farmer's units (see the last rule).
- Only state numbers that come from your tools. Never invent data. If a tool lacks something, say what is missing.
- When the farmer reports something (rain, pests, planting, yields, irrigation), save it with log_observation and thank them.
- When they change their goals, irrigation or crops, call update_farm_profile, then re-run compare_rotations if relevant.
- For rotation questions, call compare_rotations and explain the top options: why, one benefit, one risk to check locally.
- You support, not replace, the farmer's judgment: when you recommend a change, remind them to check with their local \
extension agent or agronomist. Do not lecture.
- Reply in the language the farmer writes in (many U.S. farm workers write in Spanish). In Spanish say "secano" \
for dryland and "regadío" for irrigated (never "temporal"), "soja", and "técnico agrícola" for extension agent.
- Share the report link when they want details, maps or charts.
- Below these rules you get a snapshot of THIS farmer's field: profile, climate, the 7-day weather outlook and \
this week's watering numbers. When it covers the question (frost, heat, rain, should I water…), answer from it \
directly without calling a tool. Call tools for anything else: comparing rotations, saving reports, profile changes, \
or details not in the snapshot.
- Voice notes reach you already transcribed; answer them like any message.
- If the snapshot shows a perennial/tree crop (orchard, vineyard, olives, nuts), don't compare annual rotations: \
help with watering, frost and heat, and cover crops between the rows (search_library).
- Research you can cite: following a legume, the next crop yields ~23% more, ~16% after another non-legume crop, \
~20% on average vs monoculture; legume rotations cut corn/wheat nitrogen fertilizer 41–46% (Mudare et al. 2025, \
Nature Communications, 3,663 field trials). The snapshot also lists the country's main crops (FAOSTAT) and nearby \
long-term rotation experiments (DRIVES).
- For practical how-to (cover crops, soil building, rotation planning, pests and diseases, organic methods) search \
your library first (search_library). Paraphrase what it says in your own short words and cite it briefly, e.g. \
"(SARE, Managing Cover Crops Profitably, p. 85)". Never copy long passages.
- You can search the web (web_search) for general agronomy knowledge your field data doesn't cover: crops outside \
your list, rotation research, pests and diseases, practices for their region. Search only when it really helps \
(at most 2 searches), keep the answer short, and name the source in a few words (e.g. "según la Universidad de \
California"). Numbers about THEIR field still come only from the FarmPartner tools.
- When the farmer sends a crop photo: say what you can actually see (crop, growth stage, color, visible wilting, \
spots, insects or damage) as a visual impression, not a diagnosis. Name the 1-2 most likely causes, what to check in \
the field to confirm (e.g. underside of leaves, how many plants, roots), and when it matters, connect it to their \
field data with a tool (e.g. heat or dry soil → irrigation_advice). Save clear pest or disease sightings with \
log_observation. If the photo is unclear or not a crop, ask for a closer photo of the affected plants."""

PRIORITY_ENUM = ["save_water", "soil_health", "income", "less_erosion", "forage"]

TOOLS = [
    {"name": "get_field_conditions",
     "description": "Climate, soil, satellite crop history, frost dates, heat trend and the farmer's own observations for their field.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "compare_rotations",
     "description": "Score the farmer's current rotation against the best alternatives for their climate, soil and goals. "
                    "Returns 3 strategies with scores, why, benefits, risks and information gaps.",
     "input_schema": {"type": "object", "properties": {
         "priorities": {"type": "array", "items": {"type": "string", "enum": PRIORITY_ENUM},
                        "description": "Override the saved goals for this comparison."},
         "irrigated": {"type": "boolean", "description": "Override whether the field is irrigated."}},
         "additionalProperties": False}},
    {"name": "irrigation_advice",
     "description": "Should they water this week? 7-day forecast rain vs crop water use, soil moisture vs normal, "
                    "and the farmer's recent rain-gauge reports.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "weather_outlook",
     "description": "Frost, hard freeze and heat (>95°F) days in the next 7 days, plus typical frost dates.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "log_observation",
     "description": "Save something the farmer observed on the field.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["rain", "irrigated", "pest", "planted", "harvested", "yield", "note"]},
         "value": {"type": "number", "description": "Amount, e.g. 1.2 for rain or 180 for bu/ac."},
         "unit": {"type": "string", "description": "e.g. in, mm, bu/ac"},
         "text": {"type": "string", "description": "Short description in the farmer's words."}},
         "required": ["kind"], "additionalProperties": False}},
    {"name": "update_farm_profile",
     "description": "Update the farmer's goals, whether the field is irrigated, the crops in their current rotation, "
                    "or constraints (equipment, livestock, market access).",
     "input_schema": {"type": "object", "properties": {
         "priorities": {"type": "array", "items": {"type": "string", "enum": PRIORITY_ENUM}},
         "irrigated": {"type": "boolean"},
         "current_rotation": {"type": "array", "items": {"type": "string", "enum": sorted(crops())},
                              "description": "Crop ids in rotation order, one per year."},
         "constraints": {"type": "object", "description": "Free-form key/values, e.g. {\"livestock\": true}"}},
         "additionalProperties": False}},
]


def _slim_comparison(res: dict, url: str) -> dict:
    return {"report_url": url, "priorities": res["priorities"], "irrigated": res["irrigated"], "strategies": [
        {k: s.get(k) for k in ("role", "name", "total", "scores", "why", "benefits", "risks", "issues")}
        | {"bare_months_per_year": s["metrics"]["bare_months_per_yr"]} for s in res["strategies"]],
        "information_gaps": res["strategies"][0]["gaps"] if res["strategies"] else [],
        "disclaimer": ex.DISCLAIMER}


TOOLS.append({
    "name": "search_library",
    "description": "Search FarmPartner's agronomy library for practical, expert how-to: SARE handbooks (Managing Cover "
                   "Crops Profitably; Building Soils for Better Crops; Crop Rotation on Organic Farms), Best4Soil's "
                   "rotation guide, a good-agricultural-practices manual (UNLP) and an organic vegetable-rotation "
                   "article. Use it for cover-crop choice, seeding and termination, soil organic matter, compaction, "
                   "rotation planning, crop families, pest/disease/nematode breaks, organic practices. Returns "
                   "passages with title and page. Put key words in English AND Spanish in the query.",
    "input_schema": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Key words, e.g. 'hairy vetch termination corn / veza terminación maíz'"}},
        "required": ["query"], "additionalProperties": False}})


def run_tool(name: str, args: dict, farmer_id: int) -> dict:
    if name == "search_library":
        from app import library
        hits = library.search(args.get("query", ""))
        return {"passages": hits} if hits else {"passages": [], "note": "Nothing relevant in the library."}
    with session() as db:
        farmer = db.get(Farmer, farmer_id)
        field = service.main_field(farmer)
        if field is None:
            return {"error": "No field yet. Ask the farmer to share a location pin or ZIP code."}
        _ = field.observations  # load what the read-only tools need, then give the connection back before any fetch
        if name not in ("compare_rotations", "log_observation", "update_farm_profile"):
            service.release(db)
        if name == "get_field_conditions":
            return service.conditions(field)
        if name == "compare_rotations":
            res = service.recommend(db, farmer, field, args.get("priorities"), args.get("irrigated"))
            return _slim_comparison(res, service.report_url(field))
        if name == "irrigation_advice":
            return service.irrigation_advice(field)
        if name == "weather_outlook":
            return service.weather_outlook(field)
        if name == "log_observation":
            o = service.log_observation(db, field, args["kind"], args.get("value"), args.get("unit"), args.get("text"))
            return {"saved": True, "id": o.id}
        if name == "update_farm_profile":
            if "priorities" in args:
                farmer.priorities = args["priorities"]
            if "irrigated" in args:
                field.irrigated = args["irrigated"]
            if args.get("current_rotation"):
                field.current_rotation = args["current_rotation"]
            if args.get("constraints"):
                farmer.constraints = {**(farmer.constraints or {}), **args["constraints"]}
            db.commit()
            return {"saved": True}
    return {"error": f"unknown tool {name}"}


def _history(farmer_id: int, limit: int = 12) -> list[dict]:
    with session() as db:
        msgs = db.query(Message).filter(Message.farmer_id == farmer_id).order_by(Message.id.desc()).limit(limit).all()
    turns: list[dict] = []
    for m in reversed(msgs):
        role = "user" if m.direction == "in" else "assistant"
        body = m.body or ("[shared a location pin]" if m.lat is not None else "")
        if not body:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n" + body
        else:
            turns.append({"role": role, "content": body})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def answer(farmer: Farmer, text: str, image: dict | None = None) -> str:
    """The inbound message must already be recorded in the message table (it is the last user turn).
    image: a Claude image content block (app/media.image_block) attached to that turn."""
    from app import ratelimit

    if config.llm_enabled() and ratelimit.allow(f"llm:{farmer.id}", ratelimit.LLM_PER_FARMER_DAY):
        try:
            return _answer_llm(farmer.id, image)
        except Exception as e:
            log.exception("LLM failed; falling back to rules")
            if config.federation_enabled() and "Token exchange failed" in str(e):
                _log_identity_claims()
    if image:
        return ("📷 Thanks, I saved your photo. Reading photos needs my AI assistant, which is offline right now. "
                "Tell me in words what you see (e.g. \"yellow leaves\", \"aphids\") and I'll log it.")
    return _answer_rules(farmer.id, text)


GCP_IDENTITY_URL = ("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
                    "?audience=https://api.anthropic.com&format=full")
_client = None


def _google_identity_token() -> str:
    """Google-signed ID token for this Cloud Run service account (format=full adds the email claim the rule matches)."""
    import httpx

    r = httpx.get(GCP_IDENTITY_URL, headers={"Metadata-Flavor": "Google"}, timeout=10)
    r.raise_for_status()
    return r.text.strip()


def _log_identity_claims():
    """On a federation 401, log which claims our Google token carries (never the token) to compare with the rule."""
    import base64

    try:
        payload = _google_identity_token().split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        keep = {k: v for k, v in claims.items() if k in ("iss", "aud", "sub", "azp", "email", "email_verified", "google")}
        log.error("WIF token claims (compare with the Claude Console federation rule): %s", json.dumps(keep))
    except Exception:
        log.exception("could not read identity token claims")


def claude_client():
    """One shared client: with federation the SDK caches the exchanged token and refreshes it before expiry."""
    global _client
    if _client is None:
        import anthropic

        if config.federation_enabled():
            _client = anthropic.Anthropic(credentials=anthropic.WorkloadIdentityCredentials(
                identity_token_provider=_google_identity_token,
                federation_rule_id=config.ANTHROPIC_FEDERATION_RULE_ID,
                organization_id=config.ANTHROPIC_ORGANIZATION_ID,
                service_account_id=config.ANTHROPIC_SERVICE_ACCOUNT_ID or None,
                workspace_id=config.ANTHROPIC_WORKSPACE_ID or None,
            ))
        else:
            _client = anthropic.Anthropic()
    return _client


WEB_TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
_web = {"ok": True}  # switched off for this process if the account doesn't allow web search


def _create(client, messages, system=None, max_tokens: int = 4000):
    """system: the farmer-specific text that follows the shared, cached instructions."""
    blocks = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]  # tools + rules: cached prefix
    if system:
        blocks.append({"type": "text", "text": system})
    kw = dict(model=config.ANTHROPIC_MODEL, max_tokens=max_tokens, system=blocks, messages=messages,
              output_config={"effort": config.ANTHROPIC_EFFORT},
              betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    if _web["ok"]:
        try:
            return client.beta.messages.create(tools=TOOLS + WEB_TOOLS, **kw)
        except Exception as e:
            if "web_search" not in str(e) and "web search" not in str(e).lower():
                raise
            log.warning("web search unavailable, continuing without it: %s", e)
            _web["ok"] = False
    return client.beta.messages.create(tools=TOOLS, **kw)


def _snapshot(fm: Farmer) -> str:
    """Compact facts about this farmer's field so common questions need no tool round trip."""
    from app.engine.features import rotation_label

    fld = service.main_field(fm) if fm else None
    if fld is None or not fld.features:
        return ""
    c = (fld.features or {}).get("climate") or {}
    snap = {
        "field": {"place": fld.place, "irrigated": fld.irrigated, "goals": fm.priorities,
                  "perennial_crop": (fm.constraints or {}).get("perennial_crop"),
                  "current_rotation": rotation_label(fld.current_rotation) if fld.current_rotation else None,
                  "report_url": service.report_url(fld)},
        "country_main_crops_FAOSTAT": [t["item"] for t in ((fld.features or {}).get("country") or {}).get("top", [])[:8]],
        "long_term_trials_nearby_DRIVES": [{k: t[k] for k in ("name", "place", "since", "km")}
                                           for t in (fld.features or {}).get("trials") or []],
        "climate_20yr": {k: c.get(k) for k in ("annual_precip_mm", "annual_et0_mm", "median_last_spring_frost",
                                               "median_first_fall_frost", "heat_days_recent5", "heat_days_early")},
    }
    for key, fn in (("weather_next_7_days", service.weather_outlook), ("watering_this_week", service.irrigation_advice)):
        try:
            snap[key] = fn(fld)
        except Exception:
            log.warning("snapshot %s unavailable", key, exc_info=True)
    return "\n\nFIELD SNAPSHOT (from FarmPartner's tools):\n" + json.dumps(snap, default=str, separators=(",", ":"))


def _system_for(farmer_id: int) -> str:
    """Farmer-specific instructions and data (sent after the cached shared prefix)."""
    from app import units
    with session() as db:
        fm = db.get(Farmer, farmer_id)
        metric = fm is not None and units.preferred(fm) == "metric"
        note = ((fm.constraints or {}).get("goal_note") if fm else None)
        fld = service.main_field(fm) if fm else None
        _ = fld.observations if fld else None
        service.release(db)  # the snapshot fetches the forecast: don't hold a connection meanwhile
        snapshot = _snapshot(fm) if fm else ""
    goal = f"\n- The farmer's own goal, in their words: “{note}”. Keep it in mind." if note else ""
    return "This farmer:" + goal + ("\n- Units: this farmer uses METRIC. Tools report inches, °F and acres: convert to mm, °C and "
                     "hectares in your reply (1 in = 25.4 mm; °C = (°F − 32) × 5/9; 1 acre = 0.405 ha)." if metric else
                     "\n- Units: inches, °F and acres.") + snapshot


def _answer_llm(farmer_id: int, image: dict | None = None) -> str:
    import time

    t0 = time.monotonic()
    client = claude_client()
    system = _system_for(farmer_id)
    t_ctx = time.monotonic() - t0
    messages = _history(farmer_id)
    while messages and messages[-1]["role"] == "assistant":
        messages.pop()  # e.g. "📷 Got it — taking a look…" sent while we work; the request must end on the farmer's turn
    if not messages:
        return HELP
    if image:
        messages[-1]["content"] = [image, {"type": "text", "text": messages[-1]["content"]}]
    for calls in range(1, 9):
        resp = _create(client, messages, system)
        if resp.stop_reason == "pause_turn":  # a long web search: send the turn back and the server resumes it
            messages.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason == "refusal":
            return "Sorry, I can't help with that one. I can help with rotations, watering, frost and heat on your field."
        if resp.stop_reason != "tool_use":
            u = getattr(resp, "usage", None)
            log.info("answer in %.1fs (context %.1fs, %d calls, cache read %s / write %s tokens)",
                     time.monotonic() - t0, t_ctx, calls, getattr(u, "cache_read_input_tokens", "?"),
                     getattr(u, "cache_creation_input_tokens", "?"))
            return "".join(b.text for b in resp.content if b.type == "text").strip() or HELP
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            try:
                out, err = run_tool(b.name, dict(b.input or {}), farmer_id), False
            except Exception as e:
                log.exception("tool %s failed", b.name)
                out, err = {"error": str(e)}, True
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str),
                            "is_error": err})
        messages.append({"role": "user", "content": results})
    return "Sorry, that took too long. Could you ask again in a simpler way?"


def _answer_rules(farmer_id: int, text: str) -> str:
    t = text.lower()
    if re.search(r"\b(water|irrigat|riego|regar)", t) and "?" in t or re.search(r"should i (water|irrigate)", t):
        r = run_tool("irrigation_advice", {}, farmer_id)
        return (f"💧 {r['advice']}\nNext 7 days: {r['expected_rain_in']} in rain vs {r['expected_crop_water_use_in']} in crop water use."
                + (f"\nSoil moisture: {r['root_zone_soil_moisture_vs_normal_pct']:+d}% vs normal (NASA)." if r.get("root_zone_soil_moisture_vs_normal_pct") is not None else ""))
    if re.search(r"frost|freeze|helada|cold|heat|hot|calor", t):
        w = run_tool("weather_outlook", {}, farmer_id)
        parts = []
        parts.append("❄️ Frost possible: " + ", ".join(f"{d['date']} ({d['low_F']}°F)" for d in w["frost_days"]) if w["frost_days"] else "No frost in the next 7 days.")
        if w["heat_days_above_95F"]:
            parts.append("🔥 Heat >95°F: " + ", ".join(d["date"] for d in w["heat_days_above_95F"]))
        parts.append(f"Typical frost dates here: last {w['typical_last_spring_frost']}, first {w['typical_first_fall_frost']}.")
        return "\n".join(parts)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(in|inch|inches|\"|mm)\b.*\brain|\brain\w*\D*(\d+(?:\.\d+)?)\s*(in|inch|inches|\"|mm)", t)
    if m:
        val = float(m.group(1) or m.group(3))
        unit = (m.group(2) or m.group(4))
        run_tool("log_observation", {"kind": "rain", "value": val, "unit": "mm" if unit == "mm" else "in", "text": text}, farmer_id)
        return f"🌧️ Saved {val} {'mm' if unit == 'mm' else 'in'} of rain. I'll use your gauge alongside the satellite numbers. Thanks!"
    if re.search(r"\b(saw|seeing|found|pest|bug|aphid|worm|beetle|weevil|disease|rust|mold)\b", t):
        run_tool("log_observation", {"kind": "pest", "text": text}, farmer_id)
        return "🐛 Noted — saved to your field log. If it spreads, your county extension office can confirm the pest and threshold."
    if re.search(r"\b(planted|sowed|seeded|harvest)", t):
        run_tool("log_observation", {"kind": "planted" if "harvest" not in t else "harvested", "text": text}, farmer_id)
        return "✅ Saved to your field log."
    pr, irr, named = parse_priorities(t) if re.search(r"water|soil|income|erosion|forage|money|profit", t) else [], parse_irrigated(t), crops_in_text(t)
    if re.search(r"rotat|plant|grow|crop|next|after|option|recommend|suggest|sembrar", t) or pr or irr is not None:
        args = {}
        if pr:
            args["priorities"] = pr
        if irr is not None:
            args["irrigated"] = irr
        if args:
            run_tool("update_farm_profile", args, farmer_id)
        res = run_tool("compare_rotations", {}, farmer_id)
        if "error" in res:
            return res["error"]
        lines = []
        for i, s in enumerate(res["strategies"]):
            tag = "Today" if i == 0 else f"Option {i}"
            lines.append(f"*{tag}: {s['name'].replace('Your current rotation: ', '')}* — {s['total']:.0f}/100")
            if i:
                lines.append("  " + (s["why"][0] if s["why"] else ""))
        lines.append(f"Details: {res['report_url']}")
        lines.append(ex.DISCLAIMER)
        return "\n".join(lines)
    if "report" in t:
        with session() as db:
            return "📊 " + service.report_url(service.main_field(db.get(Farmer, farmer_id)))
    return HELP


def prewarm() -> None:
    """Startup/keep-alive: exchange the Claude credentials and write the shared prompt prefix to the cache with a
    max_tokens=0 request (prefill only, no output billed), so the first farmer doesn't pay for either."""
    if not config.llm_enabled():
        return
    resp = _create(claude_client(), [{"role": "user", "content": "ping"}], max_tokens=0)
    u = getattr(resp, "usage", None)
    log.info("Claude prewarm: cache write %s / read %s tokens", getattr(u, "cache_creation_input_tokens", "?"),
             getattr(u, "cache_read_input_tokens", "?"))


_fast = {"ok": True}


def quick(system: str, user, max_tokens: int = 1500) -> str:
    """One short Claude call for translation/classification on the fast model (falls back to the main model).
    user: text, or a list of content blocks (e.g. an image block plus text)."""
    client = claude_client()
    kw = dict(max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}],
              betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    resp = None
    if _fast["ok"] and config.FAST_MODEL:
        try:
            resp = client.beta.messages.create(model=config.FAST_MODEL, **kw)
        except Exception as e:
            log.warning("fast model unavailable (%s); using %s", e, config.ANTHROPIC_MODEL)
            _fast["ok"] = False
    if resp is None:
        resp = client.beta.messages.create(model=config.ANTHROPIC_MODEL, output_config={"effort": "low"}, **kw)
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def identify_crop(image: dict) -> str:
    """What crop is in this farmer's photo, as a short English name ("corn", "pistachio trees"), or ""."""
    if not config.llm_enabled():
        return ""
    try:
        name = quick("You identify crops in farmers' photos. Reply with ONLY the crop's common English name in 1-3 "
                     "words (e.g. 'corn', 'winter wheat', 'pistachio trees', 'olive trees'), or 'unknown' if no crop "
                     "is clearly visible.", [image, {"type": "text", "text": "Which crop is this?"}], max_tokens=20)
    except Exception:
        log.warning("crop identification failed", exc_info=True)
        return ""
    name = name.strip().strip(".").lower()
    return "" if not name or "unknown" in name or len(name) > 40 else name

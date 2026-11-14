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
WhatsApp *bold* is fine. Use inches, °F and acres.
- Only state numbers that come from your tools. Never invent data. If a tool lacks something, say what is missing.
- When the farmer reports something (rain, pests, planting, yields, irrigation), save it with log_observation and thank them.
- When they change their goals, irrigation or crops, call update_farm_profile, then re-run compare_rotations if relevant.
- For rotation questions, call compare_rotations and explain the top options: why, one benefit, one risk to check locally.
- You support, not replace, the farmer's judgment: when you recommend a change, remind them to check with their local \
extension agent or agronomist. Do not lecture.
- Reply in the language the farmer writes in (many U.S. farm workers write in Spanish).
- Share the report link when they want details, maps or charts."""

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


def run_tool(name: str, args: dict, farmer_id: int) -> dict:
    with session() as db:
        farmer = db.get(Farmer, farmer_id)
        field = service.main_field(farmer)
        if field is None:
            return {"error": "No field yet. Ask the farmer to share a location pin or ZIP code."}
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


def answer(farmer: Farmer, text: str) -> str:
    """The inbound message must already be recorded in the message table (it is the last user turn)."""
    if config.llm_enabled():
        try:
            return _answer_llm(farmer.id)
        except Exception:
            log.exception("LLM failed; falling back to rules")
    return _answer_rules(farmer.id, text)


def _answer_llm(farmer_id: int) -> str:
    import anthropic

    client = anthropic.Anthropic()
    messages = _history(farmer_id)
    if not messages:
        return HELP
    for _ in range(6):
        resp = client.beta.messages.create(
            model=config.ANTHROPIC_MODEL, max_tokens=4000, system=SYSTEM, tools=TOOLS, messages=messages,
            output_config={"effort": config.ANTHROPIC_EFFORT},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            return "Sorry, I can't help with that one. I can help with rotations, watering, frost and heat on your field."
        if resp.stop_reason != "tool_use":
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

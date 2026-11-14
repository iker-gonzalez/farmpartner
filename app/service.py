"""Business logic shared by the chat agent, the onboarding flow, the web report and the alert job."""
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PUBLIC_BASE_URL
from app.data import forecast as fc
from app.db import Alert, CropHistory, Farmer, Field, Observation, Recommendation, now
from app.engine import explain as ex
from app.engine.features import add_ndvi, build_features, crop_name
from app.engine.scoring import canonical_cycle, compare


def get_or_create_farmer(db: Session, phone: str, channel: str) -> Farmer:
    f = db.scalar(select(Farmer).where(Farmer.phone == phone))
    if not f:
        f = Farmer(phone=phone, channel=channel, priorities=[], constraints={})
        db.add(f)
        db.commit()
    return f


def main_field(farmer: Farmer) -> Field | None:
    return farmer.fields[-1] if farmer.fields else None


def report_url(field: Field) -> str:
    return f"{PUBLIC_BASE_URL}/r/{field.public_id}"


SATELLITE_KEYS = ("smap", "ecostress", "appeears", "ndvi")


def release(db: Session) -> None:
    """End the open transaction so the pooled connection goes back while we wait on outside APIs (objects stay usable:
    expire_on_commit=False). Holding it through a 30 s NASA pull starved the 5-connection pool and stalled chats."""
    db.commit()


def analyze_field(db: Session, field: Field, with_ndvi: bool = False) -> dict:
    release(db)
    f = build_features(field.lat, field.lon, with_ndvi=with_ndvi)
    for k in SATELLITE_KEYS:  # a refresh must not drop slower satellite results that already arrived
        if (field.features or {}).get(k) and not f.get(k):
            f[k] = field.features[k]
    if (field.features or {}).get("ndvi") and f.get("ndvi") is None:
        f["sources"] = sorted(set(f["sources"]) | {s for s in field.features.get("sources", []) if "MODIS" in s})
    field.features, field.features_at = f, now()
    rot = f.get("rotation") or {}
    db.query(CropHistory).filter(CropHistory.field_id == field.id, CropHistory.source == "cdl").delete()
    for h in rot.get("sequence", []):
        db.add(CropHistory(field_id=field.id, year=h["year"], crop=h.get("crop"), category=h.get("category"), source="cdl"))
    if not field.current_rotation and rot.get("pattern"):
        field.current_rotation = canonical_cycle(rot["pattern"])
    db.commit()
    return f


def enrich_ndvi(db: Session, field: Field) -> None:
    if field.features and not field.features.get("ndvi"):
        release(db)
        field.features = add_ndvi(dict(field.features))
        db.commit()


def recommend(db: Session, farmer: Farmer, field: Field, priorities: list[str] | None = None,
              irrigated: bool | None = None) -> dict:
    priorities = priorities if priorities is not None else (farmer.priorities or [])
    irrigated = field.irrigated if irrigated is None else irrigated
    f = field.features or analyze_field(db, field)
    f = _with_farmer_rain(f, field)
    result = ex.explain(compare(f, priorities, irrigated, current=field.current_rotation or None), f)
    db.add(Recommendation(field_id=field.id, priorities=priorities, result=result))
    db.commit()
    return result


def _with_farmer_rain(f: dict, field: Field) -> dict:
    """Farmer rain-gauge reports are kept as ground truth next to the satellite numbers (shown, not blended)."""
    rain = [o for o in field.observations if o.kind == "rain" and o.value is not None]
    if rain:
        f = dict(f)
        f["farmer_rain_reports"] = [{"date": o.created_at.date().isoformat(), "inches": o.value} for o in rain[-10:]]
    return f


def conditions(field: Field) -> dict:
    f = field.features or {}
    c = f.get("climate") or {}
    return {
        "place": field.place, "irrigated": field.irrigated,
        "current_rotation": [crop_name(x) for x in field.current_rotation or []],
        "crop_history_from_satellite": [{"year": h.year, "crop": h.category} for h in field.history],
        "evidence": ex.evidence(f),
        "season_growing_degree_days_C": c.get("season_gdd_c"),
        "soil": f.get("soil"),
        "farmer_observations": [{"kind": o.kind, "value": o.value, "unit": o.unit, "text": o.text,
                                 "date": o.created_at.date().isoformat()} for o in field.observations[-10:]],
        "data_sources": f.get("sources", []),
        "report_url": report_url(field),
    }


def _in_season(month: int) -> bool:
    return 5 <= month <= 9


def irrigation_advice(field: Field) -> dict:
    days = fc.forecast(field.lat, field.lon, 7)
    rain = sum(d["precip"] or 0 for d in days)
    et0 = sum(d["et0"] or 0 for d in days)
    month = dt.date.today().month
    kc = 1.0 if _in_season(month) else 0.4  # rough crop coefficient: full canopy in summer, little demand off-season
    demand = et0 * kc
    deficit = demand - rain
    recent_obs = [o for o in field.observations if o.kind == "rain" and o.value is not None
                  and (now() - o.created_at).days <= 7]
    farmer_rain_in = sum(o.value for o in recent_obs)
    deficit -= farmer_rain_in * 25.4 * 0.5  # recent rain still in the root zone (half credit)
    c = (field.features or {}).get("climate") or {}
    wet_now, wet_norm = c.get("soil_wetness_now"), c.get("soil_wetness_normal")
    if field.irrigated is False:
        advice = ("Dryland field: rain this week should cover crop water use." if deficit <= 5 else
                  f"Dryland field: crops may use ~{ex.inch(deficit)} in more than the rain this week, drawing on stored "
                  "soil water. Watch for stress; keep residue on the surface to save moisture.")
    elif deficit <= 5:
        advice = "No need to irrigate this week — expected rain covers crop water use."
    elif not _in_season(month):
        advice = "Off-season: crop water demand is low. Irrigation is usually not needed now."
    elif deficit < 20:
        advice = f"Light irrigation (~{ex.inch(deficit)} in) later in the week if the soil feels dry."
    else:
        advice = f"Plan to apply about {ex.inch(deficit)} in this week, split into 2 sets."
    return {
        "next_7_days": [{"date": d["date"], "rain_in": ex.inch(d["precip"]), "crop_water_use_in": ex.inch((d["et0"] or 0) * kc),
                         "high_F": ex.fahr(d["tmax"]), "low_F": ex.fahr(d["tmin"])} for d in days],
        "expected_rain_in": ex.inch(rain), "expected_crop_water_use_in": ex.inch(demand),
        "farmer_reported_rain_last_7d_in": round(farmer_rain_in, 2),
        "deficit_in": ex.inch(max(deficit, 0)),
        "root_zone_soil_moisture_vs_normal_pct": round(100 * (wet_now / wet_norm - 1)) if wet_now and wet_norm else None,
        "smap_root_zone_percentile": ((field.features or {}).get("smap") or {}).get("rootzone_percentile"),
        "ecostress_latest_stress_index": (((field.features or {}).get("ecostress") or {}).get("latest") or {}).get("esi"),
        "advice": advice,
        "sources": ["Open-Meteo 7-day forecast (FAO-56 ET0)", "NASA POWER GWETROOT soil wetness",
                    "NASA SMAP L4 root-zone soil moisture", "NASA ECOSTRESS evaporative stress"],
        "caveat": "Crop coefficient is generic; adjust for your crop stage and soil.",
    }


def weather_outlook(field: Field) -> dict:
    days = fc.forecast(field.lat, field.lon, 7)
    frost = [d for d in days if d["tmin"] is not None and d["tmin"] <= 0]
    hard = [d for d in days if d["tmin"] is not None and d["tmin"] <= -2.2]
    heat = [d for d in days if d["tmax"] is not None and d["tmax"] >= 35]
    c = (field.features or {}).get("climate") or {}
    return {
        "frost_days": [{"date": d["date"], "low_F": ex.fahr(d["tmin"])} for d in frost],
        "hard_freeze_days": [d["date"] for d in hard],
        "heat_days_above_95F": [{"date": d["date"], "high_F": ex.fahr(d["tmax"])} for d in heat],
        "typical_last_spring_frost": c.get("median_last_spring_frost"),
        "typical_first_fall_frost": c.get("median_first_fall_frost"),
        "source": "Open-Meteo forecast; frost dates from 20 years of NASA POWER",
    }


def log_observation(db: Session, field: Field, kind: str, value: float | None = None, unit: str | None = None,
                    text: str | None = None) -> Observation:
    if kind == "rain" and value is not None and unit in ("mm",):
        value, unit = round(value / 25.4, 2), "in"
    o = Observation(field_id=field.id, kind=kind, value=value, unit=unit or ("in" if kind == "rain" else None), text=text)
    db.add(o)
    db.commit()
    return o


def check_alerts(db: Session, field: Field) -> list[str]:
    """Daily job: frost, heat and dry-spell alerts from the 7-day forecast. One alert per kind per day."""
    out = []
    w = weather_outlook(field)
    today = dt.date.today().isoformat()
    candidates = []
    if w["hard_freeze_days"]:
        candidates.append(("frost", f"❄️ Hard freeze expected {w['hard_freeze_days'][0]} at your field. Protect tender crops/livestock water."))
    elif w["frost_days"]:
        d = w["frost_days"][0]
        candidates.append(("frost", f"🌡️ Frost possible {d['date']} (low {d['low_F']}°F)."))
    if w["heat_days_above_95F"]:
        d = w["heat_days_above_95F"][0]
        candidates.append(("heat", f"🔥 Heat stress: {d['high_F']}°F expected {d['date']}. Crops need more water that week."))
    if field.irrigated and _in_season(dt.date.today().month):
        ia = irrigation_advice(field)
        if (ia["deficit_in"] or 0) >= 0.8:
            candidates.append(("irrigate", f"💧 {ia['advice']}"))
    for kind, body in candidates:
        exists = db.scalar(select(Alert).where(Alert.field_id == field.id, Alert.kind == kind, Alert.for_date == today))
        if not exists:
            db.add(Alert(field_id=field.id, kind=kind, for_date=today, body=body))
            out.append(body)
    db.commit()
    return out


# --- SMAP + ECOSTRESS via NASA AppEEARS (asynchronous) -------------------------------------------------
SMAP_SOURCE = "NASA SMAP L4 soil moisture 9 km (AppEEARS)"
ECO_SOURCE = "NASA ECOSTRESS Evaporative Stress Index 70 m (AppEEARS)"


PENDING = ("queued", "pending", "processing")


def _jobs(f: dict) -> dict:
    a = f.get("appeears") or {}
    return {} if "task_id" in a else dict(a)  # older single-job shape: resubmit as split jobs


def satellite_pending(f: dict) -> bool:
    return any((j or {}).get("status") in PENDING for j in _jobs(f).values())


def request_satellite(db: Session, field: Field, max_age_days: int = 7) -> list[str]:
    """Submit AppEEARS jobs (SMAP, ECOSTRESS) unless pending or fresh. Returns the ids of active jobs."""
    from app.data import appeears

    if not appeears.enabled():
        return []
    f = dict(field.features or {})
    jobs = _jobs(f)
    fresh_after = (now() - dt.timedelta(days=max_age_days)).isoformat()
    active = []
    release(db)
    for kind, (layers, days) in appeears.JOBS.items():
        j = jobs.get(kind) or {}
        if j.get("status") in PENDING or (j.get("status") == "done" and j.get("collected_at", "") > fresh_after):
            active.append(j.get("task_id"))
            continue
        try:
            tid = appeears.submit(field.lat, field.lon, f"farmpartner-{kind}-{field.public_id}", layers, days)
        except Exception:
            logging.getLogger(__name__).exception("AppEEARS submit failed (%s)", kind)
            continue
        jobs[kind] = {"task_id": tid, "status": "queued", "submitted_at": now().isoformat()}
        active.append(tid)
    f["appeears"] = jobs
    field.features = f
    db.commit()
    return active


def collect_satellite(db: Session, field: Field) -> str:
    """Check pending AppEEARS jobs; store SMAP / ECOSTRESS features as each finishes.
    Returns "processing" while any job is pending, else "done"."""
    from app.data import appeears

    f = dict(field.features or {})
    jobs = _jobs(f)
    release(db)
    for kind, j in jobs.items():
        if not j or j.get("status") not in PENDING:
            continue
        j = dict(j)
        j["status"] = appeears.status(j["task_id"])
        if j["status"] == "done":
            res = appeears.results(j["task_id"])
            if kind == "smap":
                feat, key, src = appeears.smap_features(res.get(appeears.SMAP, [])), "smap", SMAP_SOURCE
            else:
                feat, key, src = appeears.ecostress_features(res.get(appeears.ECO, [])), "ecostress", ECO_SOURCE
            if feat:
                f[key] = feat
                f["sources"] = sorted(set(f.get("sources", [])) | {src})
            j["collected_at"] = now().isoformat()
        jobs[kind] = j
    f["appeears"] = jobs
    field.features = f
    db.commit()
    return "processing" if satellite_pending(f) else "done"


def wait_for_satellite(field_id: int, timeout_s: int = 3600, every_s: int = 60) -> None:
    """Background: submit (if needed) and poll until the AppEEARS task finishes or times out."""
    import time

    from app.db import session as new_session

    deadline = time.time() + timeout_s
    with new_session() as db:
        fld = db.get(Field, field_id)
        if not fld or not request_satellite(db, fld):
            return
    while time.time() < deadline:
        time.sleep(every_s)
        with new_session() as db:
            fld = db.get(Field, field_id)
            if fld is None:  # the farmer sent RESET or a new pin: this field is gone
                return
            try:
                if collect_satellite(db, fld) != "processing":
                    return
            except Exception:
                logging.getLogger(__name__).exception("AppEEARS collect failed")

# 🌾 FarmPartner

**A WhatsApp/SMS farm advisor built on open NASA Earth data.** Built for NASA Space Apps 2026, challenge
*Field Shift: Adapting Farms with NASA Data*.

A farmer sends a location pin (or a ZIP code over SMS). FarmPartner pulls 20 years of NASA climate, soil-moisture and
vegetation data for that field and reads the field's crop history from USDA satellite crop maps. It then compares the
farmer's **current rotation with two alternatives** chosen for their climate and their goal. For each strategy it shows
*why* it was suggested, the *evidence* (with its source), *benefits*, *information gaps* and *risks* to check locally.
After onboarding, the farmer can keep chatting: "should I water this week?", "frost coming?", "got 1 inch of rain".
Their reports are stored as ground truth next to the satellite data.

> FarmPartner supports, and does not replace, farmer judgment and local agronomic expertise.

## Onboarding in three messages

```
Farmer: hi
Bot:    👋 … share your field location (📎 → Location) or text your ZIP code
Farmer: 📍 (pin)
Bot:    🛰️ Here's what NASA sees at Finney County, KS: ~21.5 in rain vs ~51 in evaporation demand,
        48 days >95°F a year lately (was 45) … Satellite crop maps show 2021 fallow, 2022 wheat, 2023 sorghum…
        Looks like Winter wheat → Grain sorghum → Fallow. Is that right? Add "irrigated" or "dryland".
Farmer: yes, dryland
Bot:    What matters most? 1 Save water 2 Soil health 3 Income 4 Less erosion 5 Forage
Farmer: 2
Bot:    Today: Wheat → Sorghum → Fallow — 48/100
        Option 1: Wheat + summer cover → Sorghum — 69/100 · Better soil health (+43) · ⚠️ check sorghum buyers
        Option 2: Wheat → Canola → Sorghum — 62/100 …  Full report: https://…/r/<id>
```

## Data sources

| Source | Used for | Access |
|---|---|---|
| **NASA POWER** daily (MERRA-2 T2M_MAX/MIN, PRECTOTCORR, GWETROOT) | 20-yr rainfall, evaporation demand (Hargreaves ET₀), frost dates, growing degree days, heat-stress trend, heavy-rain days, root-zone soil wetness anomaly | REST, no key |
| **NASA MODIS MOD13Q1** NDVI 250 m (ORNL DAAC) | Months the field is bare (the cover-crop window), crop vigor timeline | REST, no key (slow, fetched in background) |
| **USDA NASS Cropland Data Layer** 30 m | Crop history per year → auto-detects the current rotation | REST, no key |
| **USDA NRCS SSURGO** (Soil Data Access, behind Web Soil Survey) | Organic matter, texture, slope, erodibility (K), drainage | REST, no key |
| **NASA GRACE/GRACE-FO** | Water storage / groundwater trend | Optional: drop `data/grace_trends.csv` (`lat,lon,trend_cm_per_yr,period`); otherwise listed as a gap |
| Open-Meteo forecast | 7-day rain, ET₀, frost and heat for alerts and irrigation advice | REST, no key |
| U.S. Census geocoder, Zippopotam | County name, ZIP → location | REST, no key |

Only derived features are stored, never raw rasters.

## How it works

```
WhatsApp / SMS ─► Twilio ─► POST /twilio/webhook ─► app/chat.py
                                                     ├─ onboarding.py   (pin/ZIP → confirm crops → goal)
                                                     └─ agent.py        (Claude + 6 tools, or a keyword fallback)
                                                          │
                     service.py ──► engine/features.py   (NASA/USDA fetchers in parallel, disk cache)
                                ──► engine/scoring.py    (rule-based rotation scoring; 7 sub-scores)
                                ──► engine/explain.py    (why / evidence / benefits / gaps / risks, inches & °F)
                                ──► db.py                (farmer, field, crop_history, observation, recommendation, message, alert)
GET /r/<id>  field report (Leaflet map, rain-vs-ET₀ chart, NDVI chart, 3 strategy cards)
GET /        WhatsApp-style web simulator (same backend), for demos without a phone
```

**Scoring is deterministic and explainable.** `seeds/crops.json` holds crop traits: water need, drought and heat
tolerance, nitrogen fixing, rooting depth, residue, pests and planting months. `seeds/rotations.json` holds 16
rotation templates. Each rotation gets seven 0–100 sub-scores:
- **water**: water need vs effective rain
- **soil**: residue, legumes, cover crops, crop diversity, deep roots, minus a fallow penalty
- **erosion**: bare months per year × site risk from heavy-rain days, slope and K-factor
- **pest**: penalties for the same crop or same plant family back-to-back
- **resilience**: heat and drought tolerance vs the site's climate
- **income**: a relative market index
- **forage**

Each sub-score is weighted by the farmer's goals. Rotations that don't fit the site are filtered out (growing degree
days, or rainfall when the field is dryland). The farmer always sees three strategies: what they do today, the best
match for their goal, and the best soil builder. Claude only rephrases tool output and never invents numbers.

## Run it

```bash
uv sync
cp .env.example .env               # optional: ANTHROPIC_API_KEY, Twilio credentials
uv run python scripts/warm_cache.py   # pre-fetch demo farms (MODIS takes a few minutes)
uv run uvicorn app.main:app --reload
open http://localhost:8000          # web chat simulator: tap "Garden City, KS"
uv run pytest                       # offline tests using real-data fixtures
```

**WhatsApp:** in the Twilio console, set up the WhatsApp Sandbox. Expose the server with `ngrok http 8000`, then set
the sandbox "When a message comes in" URL to `https://<ngrok>/twilio/webhook` and set `PUBLIC_BASE_URL` to that ngrok
URL. Farmers join with the sandbox code. **SMS:** point a Twilio number's messaging webhook at the same URL.
**Alerts:** a daily cron calls `curl -X POST -d token=$ADMIN_TOKEN https://<host>/admin/alerts/run` to send frost,
heat and irrigation alerts. WhatsApp only allows free-form messages within 24 h of the farmer's last message;
production needs approved templates.

## Known limits (said out loud in the app, too)

- Crop traits and scoring weights are hackathon approximations from public extension guidance. They need
  agronomist review.
- POWER is a ~50 km model grid, and a MODIS pixel (250 m) can mix fields. CDL can mislabel some years.
- No prices or yields yet. "Income" is a relative index, and the bot asks farmers for local prices and buyers.
- U.S.-deepest: CDL and SSURGO are U.S.-only. Climate, NDVI and the forecast work worldwide, and outside the U.S. the
  farmer types their crops.
- SMAP (9 km soil moisture) and ECOSTRESS (ET) need Earthdata login and are not wired in yet. POWER's GWETROOT stands
  in for soil moisture.

MIT licensed.

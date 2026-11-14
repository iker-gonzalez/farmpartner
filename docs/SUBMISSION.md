# FarmPartner: Space Apps 2026 submission (draft for the team to edit)

**Challenge:** Field Shift: Adapting Farms with NASA Data
**Try it:** https://farmpartner.space · WhatsApp **+1 620 318 1728** (text "hi") · browser demo https://farmpartner.space/chat
**Sample field report:** https://farmpartner.space/sample · **Code (MIT):** https://github.com/iker-gonzalez/farmpartner

---

## High-level summary (≈150 words)

FarmPartner is a free farm advisor that lives in WhatsApp and SMS, the apps farmers already use. A farmer sends a
location pin, confirms the crops we detected from space, and picks one goal. In about 30 seconds, FarmPartner reads
20 years of NASA climate data, MODIS vegetation, GRACE groundwater trends and USDA crop and soil maps for that
exact field. It then compares the farmer's **current rotation with two alternatives** that fit their climate, water
and goal. Each strategy comes with *why it was suggested, the evidence and its NASA source, benefits, information
gaps, and risks to check locally*. NASA SMAP soil moisture and ECOSTRESS crop stress arrive minutes later and feed
watering advice. Afterwards farmers keep chatting ("should I water this week?", "frost coming?", "got an inch of
rain"), and what they report is stored as ground truth next to the satellite data. FarmPartner supports, and never
replaces, the farmer's judgment.

## The problem

High Plains farmers sit on a shrinking Ogallala aquifer with more heat days and more erratic rain. Rotation decisions
(what to grow next, when to leave the soil bare, how much to irrigate) are made with little field-level evidence.
NASA's data could help, but it lives in portals, rasters and acronyms that a busy farmer will never open.

## What it does

1. **No-form onboarding in 3 taps.** A location pin gives us the field. USDA Cropland Data Layer history detects the
   current rotation ("Looks like wheat → sorghum → fallow, right?"). One or more goals: save water, soil health,
   income, less erosion, forage, or the farmer's own words. Works in 46 languages, by text or voice note.
2. **Three strategies compared:** what the farmer does today and the two best alternatives, found by searching every
   2- and 3-year sequence that fits the site (with second crops and covers in the gaps). Each gets 7 transparent sub-scores (water, soil, erosion, pest-cycle break, heat/drought resilience, income, forage),
   weighted by the farmer's goals, plus the yield gain 3,663 field trials predict and the long-term experiments
   nearby that have run it for decades.
3. **Every recommendation explains itself:** why, evidence (with its data source), benefits, gaps (e.g. "SMAP is 9 km,
   not your field") and risks ("check local sorghum buyers").
4. **Bare-soil windows and vulnerable seasons:** MODIS NDVI shows the months the field is bare (the cover-crop window),
   and POWER climate data shows frost dates, heat-stress days and heavy-rain erosion risk.
5. **Ongoing advice:** watering, frost and heat questions answered with the 7-day forecast plus SMAP soil moisture and
   ECOSTRESS stress, daily alerts, and a full field report page (map, rain vs. evaporation demand, NDVI, strategy cards).
6. **Farmers feed the system:** rain gauges, pests, plantings and yields are logged as observations.

## NASA data used

| Dataset | What it tells the farmer |
|---|---|
| **NASA POWER** (MERRA-2 daily, 20 yr) | Rain vs. evaporation demand, frost dates, growing degree days, heat-stress trend, heavy-rain days, root-zone wetness |
| **MODIS MOD13Q1 NDVI** (250 m, ORNL DAAC) | Bare-soil months, crop vigor through the season |
| **GRACE / GRACE-FO** (JPL mascon RL06.3) | Long-term water-storage (groundwater) trend, e.g. −0.34 in/yr since 2002 at Garden City, KS |
| **SMAP L4** (9 km, via AppEEARS) | Root-zone and surface soil moisture now vs. earlier months |
| **ECOSTRESS ESI** (70 m, via AppEEARS) | Field-scale crop water stress across the last year |

**Other open data:** USDA NASS Cropland Data Layer (crop history), USDA NRCS SSURGO (soil organic matter, texture,
slope, erodibility), Open-Meteo forecast and elevation, U.S. Census geocoder.

## How it's built

Python/FastAPI on Google Cloud Run, Supabase Postgres, a Cloud Storage cache for raw data, and Cloud Scheduler for
daily alerts, all in Terraform. Twilio delivers a branded WhatsApp Business sender and SMS. Rotation scoring is
**deterministic and rule-based**, so every number traces back to a dataset. Claude (Anthropic) understands free-text
questions and rewrites tool outputs in plain language. It is only allowed to quote numbers returned by our tools.

## Use of AI (disclosure)

- **In the product:** Claude Opus 5.5 handles conversation (intent, extracting facts from free text, plain-language
  replies) through seven tools, including a searchable library of farm handbooks; Claude Haiku 5.5
  translates and, as a last resort, searches the web with sources. Scores and data are computed by our code, not by the model.
- **In development:** the team used Claude Code as a coding assistant. *[team: adjust to what you actually used]*
- *[team: list any AI-generated images or other assets]*

## Why it matters / impact

- Meets farmers where they are: no app, no login, works on a flip phone over SMS.
- Turns five NASA missions into one decision: what to plant next, and why.
- Honest about uncertainty: gaps and risks are first-class output, not fine print.
- Open source and global by design: climate, NDVI, SMAP and ECOSTRESS work worldwide. The U.S. gets the deepest
  results because USDA crop and soil maps exist there.

## Limits and next steps

Crop traits and weights are hackathon approximations that need agronomist review. "Income" is a relative index plus the
trial-based yield effect (no local prices yet). SMAP and GRACE are coarse. Next: extension-agent review, local price
feeds, field validation with farmers, and field polygons instead of a pin.

---

## 30-second pitch (spoken)

> "A farmer in western Kansas is pumping from an aquifer that NASA's GRACE satellites show dropping every year,
> while their soil sits bare for months. NASA has the data to help, but not in a form they'll ever open.
> FarmPartner puts it in WhatsApp. Drop a pin and we read 20 years of NASA climate, MODIS, GRACE, SMAP and
> ECOSTRESS data for that field. We detect your rotation from USDA crop maps and compare it with two better-fitting
> options, each with the evidence, the gaps and the risks. Then it keeps helping: should I water, is frost coming.
> Scan the QR and try it on your own field."

## Demo script (2 minutes)

1. farmpartner.space: show the hero, then tap **Open in WhatsApp** (or scan the QR).
2. WhatsApp: tap **Get started**, send a pin near Garden City, KS (pre-warmed), and show the detected rotation.
3. Tap **Yes, dryland**, then **Build soil health**, and show the 3 strategies in chat.
4. Open the report link: map, rain vs. ET₀, NDVI bare months, strategy cards with evidence, gaps and risks.
5. Ask "should I water this week?" (uses SMAP + ECOSTRESS), then "we got 1 inch of rain" (logged as ground truth).
6. Close on the architecture slide and "open source, MIT".

*Backup: the browser simulator at /chat runs the same flow without a phone.*

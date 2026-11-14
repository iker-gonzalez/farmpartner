"""Content of the Data & Science page (/data-and-science): every source FarmPartner reads, what it is and what it
feeds. Kept here so the page and the README can be checked against the code in one place."""

STATS = [
    ("5", "NASA missions & models"),
    ("2", "USDA field datasets"),
    ("6", "Global open data services"),
    ("3,663", "Field trials (meta-analysis)"),
    ("22", "Long-term rotation experiments"),
    ("2,991", "Handbook passages, cited by page"),
]

# Each source: tag, name, meta, what it is, what FarmPartner uses it for.
SECTIONS = [
    {"id": "nasa", "num": "01", "color": "#a8f326", "title": "NASA Earth observation",
     "intro": "Satellites and models over 20 years — the climate, water and vegetation of the field.",
     "sources": [
         ("MERRA-2 reanalysis · daily", "NASA POWER", "~50 km · 2006 → today · global · REST",
          "Rain, temperature and root-zone soil wetness. FarmPartner derives evaporation demand (Hargreaves ET₀), "
          "frost dates, growing degree days, heat-stress days and heavy-rain days.",
          "Climate fit of every rotation, frost & heat alerts"),
         ("Terra satellite · 16-day", "MODIS NDVI · MOD13Q1", "250 m · via ORNL DAAC · global",
          "How green the field is through the year: the months the soil lies bare and how vigorous the crop looks.",
          "Bare-soil months → the cover-crop window"),
         ("SPL4SMGP · 3-hourly", "SMAP L4 soil moisture", "9 km · via NASA AppEEARS · global",
          "Root-zone and surface soil moisture now versus earlier months: “25% water by volume, up from 18% in July”.",
          "Should-I-water advice"),
         ("Evaporative Stress Index · ISS", "ECOSTRESS ESI", "70 m · via NASA AppEEARS · global",
          "Actual vs potential evapotranspiration: whether the crop on this field is short of water, pass by pass.",
          "Field-scale crop water stress"),
         ("JPL mascon RL06.3 · monthly", "GRACE / GRACE-FO", "~300 km · 2002 → today · global",
          "Trend of total water storage — groundwater included — in inches per year. Over the Ogallala it is falling.",
          "Aquifer risk for thirsty, irrigated rotations"),
     ]},
    {"id": "usda", "num": "02", "color": "#f5c451", "title": "Field & farm maps",
     "intro": "What was grown on this exact field, and the soil under it — USDA, U.S. only.",
     "sources": [
         ("USDA NASS CropScape · yearly", "Cropland Data Layer", "30 m · 2017 → 2025 · U.S.",
          "The crop on the pixel each year — wheat, sorghum, fallow… — and land cover such as water or towns.",
          "Detects today's rotation without a form; rejects pins on water or towns"),
         ("USDA NRCS Soil Data Access", "SSURGO soil survey", "Soil map units · U.S.",
          "Organic matter, texture, slope, erodibility (K factor) and drainage of the field's soil.",
          "Erosion risk and soil-health scores"),
     ]},
    {"id": "global", "num": "03", "color": "#7dd3fc", "title": "Global open data",
     "intro": "Forecasts, places and what each country grows — so it works far beyond Kansas.",
     "sources": [
         ("FAO · 2020–2024", "FAOSTAT crops", "Every country · CC BY-NC-SA",
          "Harvested area and yield of each crop, country by country.",
          "Main crops shown outside the U.S.; the search only suggests crops the country grows"),
         ("Forecast · elevation · geocoding", "Open-Meteo", "Global · free",
          "7-day rain, temperature and ET₀; elevation (0.0 m means open sea); town names to coordinates.",
          "Watering, frost & heat; “I live near Bilbao”"),
         ("Places & postal codes", "GeoNames · Census · Zippopotam", "Global / U.S. county names",
          "Pin → country offline, county names, postal codes in the farmer's country.",
          "Location from a pin, a link, coordinates or a postcode"),
     ]},
    {"id": "research", "num": "04", "color": "#f0abfc", "title": "Research & experiments",
     "intro": "Peer-reviewed evidence and decades of field trials behind each suggestion.",
     "sources": [
         ("Mudare et al. 2025 · Nature Communications", "Crop-rotation meta-analysis",
          "3,663 paired field trials · 1980–2024",
          "After a legume the next crop yields ~23% more, ~16% after another crop; legume rotations cut corn and "
          "wheat N fertilizer by 41–46%.",
          "📚 Expected yield gain of every option"),
         ("Diverse Rotations Improve Valuable Ecosystem Services", "DRIVES long-term experiments",
          "22 sites · U.S., Canada, Mexico · some since 1963",
          "Rotations run for decades at research stations — corn–soy–wheat with rye or clover, alfalfa, small grains.",
          "✅ “Tested since 1981 at Lincoln, NE”"),
         ("SARE handbooks · Best4Soil · UNLP · La Fertilidad de la Tierra", "Agronomy library",
          "2,991 passages · cited by title and page",
          "Managing Cover Crops Profitably, Building Soils for Better Crops, Crop Rotation on Organic Farms and more.",
          "Practical how-to answers: cover crops, soil, pests"),
     ]},
    {"id": "ai", "num": "05", "color": "#c8c8ce", "title": "AI that cites, not invents",
     "intro": "Language models understand the farmer; the numbers always come from the data above.",
     "sources": [
         ("Anthropic · tool use", "Claude Opus 5.5", "Conversation in the farmer's language",
          "Reads the question, calls FarmPartner's tools (field data, comparisons, library) and explains in plain "
          "words. It never makes up a number.",
          "Every free-text answer, photos of crops"),
         ("Anthropic", "Claude Haiku 5.5 + web search", "Fast translation · last-resort search",
          "Translates the scripted messages into the farmer's language; searches the web only for what the data "
          "can't cover, with sources.",
          "20+ languages, Basque included"),
         ("Cloud Speech", "Google Speech-to-Text", "Voice notes · farmer's languages",
          "Turns WhatsApp voice notes into text in English, Spanish, Basque, Catalan and more.",
          "Farmers can just talk"),
     ]},
]

STEPS = [
    ("1 · Pin", "A pin, a Maps link, coordinates, a postcode or a town name."),
    ("2 · Fetch · ~30 s", "All sources in parallel; slow satellites (SMAP, ECOSTRESS) arrive minutes later."),
    ("3 · Derive", "Only derived features are stored — rain vs ET₀, bare months, frost dates — never raw rasters."),
    ("4 · Score", "Deterministic, explainable scores for today's rotation and 2–3-year alternatives found by search."),
    ("5 · Explain", "Why, evidence with its source, benefits, gaps and risks — in the farmer's language."),
]

LIMITS = [
    "NASA POWER is a ~50 km grid, SMAP 9 km and GRACE ~300 km: regional signals, not your field's corner.",
    "Crop maps and soil survey are U.S.-only; elsewhere the farmer types the crops and FAOSTAT guides the search.",
    "Crop traits and scores are approximations from public guidance — they need an agronomist's review.",
    "New combinations from the search are less proven: try them on part of a field first.",
]

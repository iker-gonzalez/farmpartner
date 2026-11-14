import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'farmpartner.db'}")
# Supabase/Heroku-style URLs → SQLAlchemy's psycopg 3 driver
for _prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(_prefix):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(_prefix):]
CACHE_DIR = Path(os.getenv("CACHE_DIR", ROOT / ".cache"))
CACHE_BUCKET = os.getenv("CACHE_BUCKET", "")  # Cloud Storage bucket for the durable raw-data cache
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
ANTHROPIC_EFFORT = os.getenv("ANTHROPIC_EFFORT", "low")
# Small, fast model for translation and quick classification (crop names, goals); falls back to ANTHROPIC_MODEL.
FAST_MODEL = os.getenv("FAST_MODEL", "claude-haiku-5-5")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")  # Twilio sandbox number
TWILIO_SMS_FROM = os.getenv("TWILIO_SMS_FROM", "")
# NASA Earthdata for AppEEARS (SMAP soil moisture, ECOSTRESS stress): token, or username + password login.
EARTHDATA_TOKEN = os.getenv("EARTHDATA_TOKEN", "")
EARTHDATA_USERNAME = os.getenv("EARTHDATA_USERNAME", "")
EARTHDATA_PASSWORD = os.getenv("EARTHDATA_PASSWORD", "")
# Cloud Scheduler calls /internal/daily with a Google-signed OIDC token for this service account + audience.
SCHEDULER_SA_EMAIL = os.getenv("SCHEDULER_SA_EMAIL", "")
SCHEDULER_AUDIENCE = os.getenv("SCHEDULER_AUDIENCE", "")
# First WhatsApp message prefilled by the landing page (sandbox: "join <code>").
WHATSAPP_PREFILL = os.getenv("WHATSAPP_PREFILL", "hi")
# Meta-approved WhatsApp template (scripts/welcome_template.py) that lets FarmPartner message first after a web opt-in.
WELCOME_CONTENT_SID = os.getenv("WELCOME_CONTENT_SID", "")


# Workload Identity Federation (Cloud Run): the service's Google identity is exchanged for a short-lived Claude
# token, so no API key is stored anywhere. These are IDs from the Claude Console, not secrets.
ANTHROPIC_FEDERATION_RULE_ID = os.getenv("ANTHROPIC_FEDERATION_RULE_ID", "")
ANTHROPIC_ORGANIZATION_ID = os.getenv("ANTHROPIC_ORGANIZATION_ID", "")
ANTHROPIC_SERVICE_ACCOUNT_ID = os.getenv("ANTHROPIC_SERVICE_ACCOUNT_ID", "")
ANTHROPIC_WORKSPACE_ID = os.getenv("ANTHROPIC_WORKSPACE_ID", "")


def federation_enabled() -> bool:
    return bool(ANTHROPIC_FEDERATION_RULE_ID and ANTHROPIC_ORGANIZATION_ID)


def llm_enabled() -> bool:
    return federation_enabled() or bool(
        os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE"))

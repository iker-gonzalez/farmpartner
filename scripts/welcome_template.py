"""Create (once) and submit FarmPartner's WhatsApp welcome template for Meta approval, or show its status.

    uv run python scripts/welcome_template.py           # create + submit if missing, then print status
Reads TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN from the environment (deploy reads them from terraform.tfvars).
Prints the Content SID to set as `welcome_content_sid` in terraform/terraform.tfvars.
"""
import os
import sys

import httpx

API = "https://content.twilio.com/v1/Content"
NAME = "farmpartner_welcome_v1"
BODY = ("👋 Hi! I'm FarmPartner, your farm advisor powered by NASA satellite data.\n\n"
        "You asked to start on farmpartner.space. In 3 quick steps I'll compare your crop rotation with "
        "options that fit your climate, soil and water: send your field location, confirm your crops, "
        "and pick your main goal.\n\nReady?")
AUTH = (os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])


def find() -> dict | None:
    url = API + "?PageSize=500"
    while url:
        page = httpx.get(url, auth=AUTH, timeout=30).raise_for_status().json()
        for c in page.get("contents", []):
            if c.get("friendly_name") == NAME:
                return c
        url = (page.get("meta") or {}).get("next_page_url")
    return None


def main():
    c = find()
    if not c:
        c = httpx.post(API, auth=AUTH, timeout=30, json={
            "friendly_name": NAME, "language": "en",
            "types": {"twilio/quick-reply": {"body": BODY, "actions": [
                {"title": "Get started", "id": "get started"}, {"title": "Not now", "id": "not now"}]}},
        }).raise_for_status().json()
        print("created", c["sid"])
    sid = c["sid"]
    status = httpx.get(f"{API}/{sid}/ApprovalRequests", auth=AUTH, timeout=30).raise_for_status().json()
    wa = status.get("whatsapp") or {}
    if not wa.get("status") or wa.get("status") == "unsubmitted":
        r = httpx.post(f"{API}/{sid}/ApprovalRequests/whatsapp", auth=AUTH, timeout=30,
                       json={"name": "farmpartner_welcome_v1", "category": "UTILITY"})
        print("submitted for WhatsApp approval ->", r.status_code, r.text[:200])
        wa = httpx.get(f"{API}/{sid}/ApprovalRequests", auth=AUTH, timeout=30).json().get("whatsapp") or {}
    print("content_sid:", sid)
    print("whatsapp approval:", wa.get("status"), "| category:", wa.get("category"),
          "| rejection:", wa.get("rejection_reason") or "-")


if __name__ == "__main__":
    sys.exit(main())

"""Erase everything FarmPartner holds about one phone number (answers a deletion request sent by e-mail).

    uv run python scripts/delete_farmer.py +34600111222          # dry run: shows what would be deleted
    uv run python scripts/delete_farmer.py +34600111222 --yes    # deletes it

Needs DATABASE_URL (e.g. DATABASE_URL="$(gcloud secrets versions access latest --secret farmpartner-database-url
--project launchpad-all-dev-01)"). Spoken-reply audio has no phone number attached and expires on its own in 30 days.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("WARMUP", "0")

from app import service  # noqa: E402
from app.db import Farmer, session  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    digits = re.sub(r"\D", "", sys.argv[1])
    if len(digits) < 7:
        raise SystemExit("Give the full number with country code, e.g. +34600111222")
    with session() as db:
        found = [f for f in db.query(Farmer).all() if re.sub(r"\D", "", f.phone or "") == digits]
        if not found:
            print("No farmer with that number.")
            return
        for fm in found:
            print(f"{fm.phone}: {len(fm.fields)} field(s), {len(fm.messages)} message(s), joined {fm.created_at:%Y-%m-%d}")
        if "--yes" not in sys.argv:
            print("Dry run. Add --yes to delete.")
            return
        for fm in found:
            service.delete_farmer(db, fm)
        print(f"Deleted {len(found)} farmer record(s).")


if __name__ == "__main__":
    main()

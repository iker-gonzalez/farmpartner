"""Collect the English text segments of the public website into app/locales/en.json ({id: English}).

Run after changing any public page:   uv run python scripts/i18n_extract.py
Then translate the new or changed ids in app/locales/<lang>.json (segments without a translation stay in English).
`--missing` lists, per language, how many segments still need translating.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("WARMUP", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app import site_i18n  # noqa: E402
from app.main import app  # noqa: E402


def english_segments() -> dict[str, str]:
    client = TestClient(app)
    seen: dict[str, str] = {}
    for path in site_i18n.PAGES:
        r = client.get(path)  # a fresh client has no language cookie: English
        r.raise_for_status()
        for text in site_i18n.collect(r.text):
            seen.setdefault(site_i18n.seg_id(text), text)
    return seen


def main():
    segs = english_segments()
    out = site_i18n.LOCALES / "en.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(segs, ensure_ascii=False, indent=1) + "\n")
    words = sum(len(t.split()) for t in segs.values())
    print(f"{len(segs)} segments, {words} words -> {out}")
    for lang in site_i18n.LANGS:  # drop translations of segments that no longer exist
        p = site_i18n.LOCALES / f"{lang}.json"
        if lang != "en" and p.exists():
            tr = json.loads(p.read_text())
            kept = {k: v for k, v in tr.items() if k in segs}
            if len(kept) != len(tr):
                p.write_text(json.dumps(kept, ensure_ascii=False, indent=1) + "\n")
                print(f"  {lang}: removed {len(tr) - len(kept)} stale translations")
    if "--missing" in sys.argv:
        for lang in site_i18n.LANGS:
            if lang != "en":
                have = site_i18n.table(lang)
                print(f"  {lang}: {sum(1 for k in segs if k not in have)} missing")


if __name__ == "__main__":
    main()

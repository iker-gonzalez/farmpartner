"""Build FarmPartner's agronomy library: page-tagged passages from handbooks the agent can search.

    uv run --with pymupdf python scripts/build_library.py <docs_dir> [--upload]

Writes data/library.json.gz (gitignored) and, with --upload, gs://$CACHE_BUCKET/library/library.json.gz. The texts are
third-party publications: they are kept out of the public repo; the agent paraphrases and cites them (title + page).
"""
import gzip
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parents[1] / "data" / "library.json.gz"
TITLES = {  # file name prefix → (short citation, language)
    "Managing-Cover-Crops-Profitably": ("Managing Cover Crops Profitably (SARE, 3rd ed.)", "en"),
    "Building-Soils-for-Better-Crops": ("Building Soils for Better Crops (SARE, 4th ed., 2021)", "en"),
    "Crop-Rotation-on-Organic-Farms": ("Crop Rotation on Organic Farms (SARE, 2009)", "en"),
    "ES_ROTACIÓN": ("Rotación de cultivos: información práctica (Best4Soil, UE H2020)", "es"),
    "Ferti_2009": ("Las rotaciones de cultivos en el huerto (La Fertilidad de la Tierra, 2009)", "es"),
    "1111-3-9040": ("Manual de buenas prácticas agrícolas (Golik et al., UNLP, 2017)", "es"),
}
TARGET = 1100  # characters per passage


def passages(path: Path, title: str, lang: str):
    doc = pymupdf.open(path)
    for pno in range(len(doc)):
        text = " ".join(doc[pno].get_text().split())
        text = re.sub(r"(\w)- (\w)", r"\1\2", text)  # re-join hyphenated line breaks
        if len(text) < 200:
            continue  # covers, blank and figure-only pages
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunk = ""
        for s in sentences:
            if len(chunk) + len(s) > TARGET and len(chunk) > 300:
                yield {"source": title, "page": pno + 1, "lang": lang, "text": chunk.strip()}
                chunk = ""
            chunk += " " + s
        if len(chunk.strip()) > 150:
            yield {"source": title, "page": pno + 1, "lang": lang, "text": chunk.strip()}


def main():
    docs = Path(sys.argv[1]).expanduser()
    out = []
    for f in sorted(docs.iterdir()):
        name = unicodedata.normalize("NFC", f.name)  # macOS stores "Ó" decomposed
        meta = next((v for k, v in TITLES.items() if name.startswith(k)), None)
        if meta and f.suffix.lower() in (".pdf", ".epub"):
            n0 = len(out)
            out.extend(passages(f, *meta))
            print(f"{meta[0]}: {len(out) - n0} passages")
    OUT.write_bytes(gzip.compress(json.dumps(out, ensure_ascii=False).encode()))
    print(f"{len(out)} passages → {OUT} ({OUT.stat().st_size // 1024} KB)")
    if "--upload" in sys.argv:
        from google.cloud import storage
        bucket = os.environ["CACHE_BUCKET"]
        storage.Client().bucket(bucket).blob("library/library.json.gz").upload_from_filename(str(OUT))
        print(f"uploaded to gs://{bucket}/library/library.json.gz")


if __name__ == "__main__":
    main()

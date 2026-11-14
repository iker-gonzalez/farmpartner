"""Searchable agronomy library for the agent (BM25 over page-tagged passages).

Sources (third-party publications, kept out of the public repo; built by scripts/build_library.py into the private
cache bucket): SARE's Managing Cover Crops Profitably, Building Soils for Better Crops and Crop Rotation on Organic
Farms; Best4Soil's rotation guide; Golik et al., Manual de buenas prácticas agrícolas (UNLP); La Fertilidad de la
Tierra. The agent paraphrases and cites them by title and page.
"""
import gzip
import json
import logging
import math
import re
import threading
import unicodedata
from collections import Counter

from app import config

log = logging.getLogger(__name__)
_lock = threading.Lock()
_index: dict = {}
STOP = set("""a an the and or of to in on for with by at from is are be as it this that these those can may will
not no do does how what which when why where your you we our their more most than into over per each also
el la los las un una unos unas y o de del al en con por para es son ser como que se su sus lo le les mas muy
sin sobre entre cuando donde cual cuales este esta estos estas""".split())


def _tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return [w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in STOP]


def _load() -> list[dict]:
    p = config.ROOT / "data" / "library.json.gz"
    if p.exists():
        return json.loads(gzip.decompress(p.read_bytes()))
    if config.CACHE_BUCKET:
        from google.cloud import storage
        blob = storage.Client().bucket(config.CACHE_BUCKET).blob("library/library.json.gz")
        if blob.exists():
            data = blob.download_as_bytes()
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            except OSError:
                pass
            return json.loads(gzip.decompress(data))
    return []


def _build():
    with _lock:
        if _index:
            return
        docs = _load()
        toks = [_tokens(d["text"]) for d in docs]
        df = Counter(w for t in toks for w in set(t))
        n = len(docs) or 1
        _index.update(docs=docs, tf=[Counter(t) for t in toks], lens=[len(t) for t in toks],
                      avg=(sum(len(t) for t in toks) / n) if docs else 1,
                      idf={w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()})
        log.info("library: %d passages loaded", len(docs))


def available() -> bool:
    try:
        _build()
    except Exception:
        log.warning("library unavailable", exc_info=True)
        return False
    return bool(_index.get("docs"))


def search(query: str, k: int = 4, k1: float = 1.4, b: float = 0.75) -> list[dict]:
    if not available():
        return []
    q = _tokens(query)
    idx = _index
    scores = []
    for i, tf in enumerate(idx["tf"]):
        s = 0.0
        for w in q:
            f = tf.get(w)
            if f:
                s += idx["idf"][w] * f * (k1 + 1) / (f + k1 * (1 - b + b * idx["lens"][i] / idx["avg"]))
        if s > 0:
            scores.append((s, i))
    scores.sort(reverse=True)
    out, seen = [], set()
    for s, i in scores:
        d = idx["docs"][i]
        key = (d["source"], d["page"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"source": d["source"], "page": d["page"], "text": d["text"][:900]})
        if len(out) == k:
            break
    return out

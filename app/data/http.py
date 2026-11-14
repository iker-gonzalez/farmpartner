"""HTTP helper with a two-level cache so demos never wait on slow public APIs twice.

L1: local JSON files in CACHE_DIR (on Cloud Run that is /tmp, i.e. memory, lost on restart).
L2: a Cloud Storage bucket (CACHE_BUCKET), durable and shared by every instance. Optional: without it, or if
    it errors, the app simply fetches live.

Objects with a TTL live under "ttl/" (the bucket expires them via a lifecycle rule); permanent ones under
"perm/". Freshness is judged from the object's upload time.
"""
import hashlib
import json
import logging
import time

import httpx

from app.config import CACHE_BUCKET, CACHE_DIR

log = logging.getLogger(__name__)
_client = httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True,
                       headers={"User-Agent": "farmpartner/0.1 (NASA Space Apps prototype)"})
_bucket = None


def _gcs_bucket():
    global _bucket
    if _bucket is None and CACHE_BUCKET:
        from google.cloud import storage

        _bucket = storage.Client().bucket(CACHE_BUCKET)
    return _bucket


def _name(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:32] + ".json"


def _gcs_get(name: str, ttl_s: float):
    try:
        b = _gcs_bucket()
        if not b:
            return None
        blob = b.get_blob(f"{'ttl' if ttl_s > 0 else 'perm'}/{name}")
        if blob is None or (ttl_s > 0 and time.time() - blob.updated.timestamp() > ttl_s):
            return None
        return blob.download_as_text()
    except Exception:
        log.warning("cache bucket read failed", exc_info=True)
        return None


def _gcs_put(name: str, ttl_s: float, text: str):
    try:
        b = _gcs_bucket()
        if b:
            b.blob(f"{'ttl' if ttl_s > 0 else 'perm'}/{name}").upload_from_string(text, content_type="application/json")
    except Exception:
        log.warning("cache bucket write failed", exc_info=True)


def cached(key: str, ttl_s: float, fn, keep=None):
    """ttl_s <= 0 means the value never changes for this key (e.g. a past year's crop map).
    keep(value) -> False skips caching (e.g. an error page served with HTTP 200)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    name = _name(key)
    p = CACHE_DIR / name
    if p.exists() and (ttl_s <= 0 or time.time() - p.stat().st_mtime < ttl_s):
        return json.loads(p.read_text())
    text = _gcs_get(name, ttl_s)
    if text is None:
        value = fn()
        if keep is not None and not keep(value):
            return value
        text = json.dumps(value)
        _gcs_put(name, ttl_s, text)
    p.write_text(text)
    return json.loads(text)


def get_json(url: str, params: dict | None = None, ttl_s: float = 0, headers: dict | None = None,
             timeout: float | None = None, keep=None):
    key = url + "?" + json.dumps(params or {}, sort_keys=True)

    def fetch():
        kw = {"timeout": timeout} if timeout else {}
        r = _client.get(url, params=params, headers=headers, **kw)
        r.raise_for_status()
        return r.json()

    return cached(key, ttl_s, fetch, keep)


def get_text(url: str, params: dict | None = None, ttl_s: float = 0, keep=None):
    key = "text:" + url + "?" + json.dumps(params or {}, sort_keys=True)

    def fetch():
        r = _client.get(url, params=params)
        r.raise_for_status()
        return r.text

    return cached(key, ttl_s, fetch, keep)


def post_form_json(url: str, data: dict, ttl_s: float = 0, keep=None):
    key = "post:" + url + "?" + json.dumps(data, sort_keys=True)

    def fetch():
        r = _client.post(url, data=data)
        r.raise_for_status()
        return r.json()

    return cached(key, ttl_s, fetch, keep)

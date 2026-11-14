"""Tiny HTTP helper with an on-disk JSON/text cache so demos never wait on slow public APIs twice."""
import hashlib
import json
import time

import httpx

from app.config import CACHE_DIR

_client = httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True,
                       headers={"User-Agent": "farmpartner/0.1 (NASA Space Apps prototype)"})


def _path(key: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")


def cached(key: str, ttl_s: float, fn):
    p = _path(key)
    if p.exists() and (ttl_s <= 0 or time.time() - p.stat().st_mtime < ttl_s):
        return json.loads(p.read_text())
    value = fn()
    p.write_text(json.dumps(value))
    return value


def get_json(url: str, params: dict | None = None, ttl_s: float = 0, headers: dict | None = None,
             timeout: float | None = None):
    key = url + "?" + json.dumps(params or {}, sort_keys=True)

    def fetch():
        kw = {"timeout": timeout} if timeout else {}
        r = _client.get(url, params=params, headers=headers, **kw)
        r.raise_for_status()
        return r.json()

    return cached(key, ttl_s, fetch)


def get_text(url: str, params: dict | None = None, ttl_s: float = 0):
    key = "text:" + url + "?" + json.dumps(params or {}, sort_keys=True)

    def fetch():
        r = _client.get(url, params=params)
        r.raise_for_status()
        return r.text

    return cached(key, ttl_s, fetch)


def post_form_json(url: str, data: dict, ttl_s: float = 0):
    key = "post:" + url + "?" + json.dumps(data, sort_keys=True)

    def fetch():
        r = _client.post(url, data=data)
        r.raise_for_status()
        return r.json()

    return cached(key, ttl_s, fetch)

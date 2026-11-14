"""In-memory rate limits. The service runs as a single Cloud Run instance (max_instances = 1), so process
memory is the shared state. Sliding windows keyed by client IP, phone and farmer."""
import threading
import time
from collections import defaultdict, deque

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()

# (max requests, window seconds)
API_PER_IP = (30, 60)            # web simulator / API: per client IP
API_PER_PHONE = (12, 60)         # per simulated phone
WEBHOOK_PER_SENDER = (15, 60)    # WhatsApp/SMS messages per sender
OPTIN_PER_IP = (5, 3600)          # web opt-ins (WhatsApp welcome sends) per IP per hour
OPTIN_PER_PHONE = (1, 86400)     # one welcome per phone per day
LLM_PER_FARMER_DAY = (150, 86400)  # Claude calls per farmer per day (cost cap)


def allow(key: str, limit: tuple[int, int]) -> bool:
    max_n, window = limit
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= max_n:
            return False
        q.append(now)
        if len(_hits) > 50_000:  # keep memory bounded
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 86400]:
                _hits.pop(k, None)
        return True


def client_ip(request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def reset():
    with _lock:
        _hits.clear()

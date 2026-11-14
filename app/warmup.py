"""Warm a new instance before the first farmer arrives: database pool, Claude credentials + prompt cache, and
translations of the fixed messages. Runs in a background thread at startup; a keep-alive re-warms the prompt cache
every 4 minutes (its default lifetime is 5)."""
import logging
import threading
import time

from sqlalchemy import text

log = logging.getLogger(__name__)
KEEPALIVE_S = 240


def _db():
    from app.db import engine
    for _ in range(2):  # open a couple of pooled connections now
        with engine.connect() as conn:
            conn.execute(text("select 1"))


def _run():
    t0 = time.monotonic()
    for name, step in (("database", _db), ("claude", _claude), ("translations", _translations)):
        try:
            step()
        except Exception:
            log.warning("warm-up step %s failed", name, exc_info=True)
    log.info("warm-up done in %.1fs", time.monotonic() - t0)
    while True:
        time.sleep(KEEPALIVE_S)
        try:
            _claude()
        except Exception:
            log.info("prompt-cache keep-alive failed", exc_info=True)


def _claude():
    from app import agent
    agent.prewarm()


def _translations():
    from app import config, i18n, onboarding
    if config.llm_enabled():
        i18n.prewarm(onboarding.static_messages())


def start():
    threading.Thread(target=_run, name="warmup", daemon=True).start()

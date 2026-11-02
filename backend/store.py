"""Disk cache + small JSON state. Everything expensive (scrapes, PDF text, LLM
calls) goes through here so a repeat request costs nothing."""

import functools
import hashlib
import json
import os
import threading
import time

from config import CACHE_DIR, DEFAULT_RULES

_lock = threading.Lock()


def _path(ns: str, key: str) -> str:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    d = os.path.join(CACHE_DIR, ns)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h + ".json")


def get(ns: str, key: str, ttl: float | None = None):
    p = _path(ns, key)
    if not os.path.exists(p):
        return None
    if ttl is not None and time.time() - os.path.getmtime(p) > ttl:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def put(ns: str, key: str, value) -> None:
    p = _path(ns, key)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, default=str)
    os.replace(tmp, p)


def cached(ns: str, ttl: float | None = None):
    """Memoise a function's JSON-serialisable result on disk, keyed by its args."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, refresh: bool = False, **kwargs):
            key = json.dumps([fn.__name__, args, kwargs], sort_keys=True, default=str)
            if not refresh:
                hit = get(ns, key, ttl)
                if hit is not None:
                    return hit
            val = fn(*args, **kwargs)
            if val is not None:
                put(ns, key, val)
            return val

        return wrapper

    return deco


# ── Watch rules (single-user prototype: one JSON file) ─────────────────────────

def _rules_path() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, "rules.json")


def load_rules() -> dict:
    rules = dict(DEFAULT_RULES)
    try:
        with open(_rules_path(), encoding="utf-8") as f:
            rules.update(json.load(f))
    except (OSError, ValueError):
        pass
    return rules


def save_rules(patch: dict) -> dict:
    with _lock:
        rules = load_rules()
        for k, v in patch.items():
            if k in DEFAULT_RULES:
                rules[k] = type(DEFAULT_RULES[k])(v) if not isinstance(DEFAULT_RULES[k], list) else list(v)
        with open(_rules_path(), "w", encoding="utf-8") as f:
            json.dump(rules, f, indent=2)
    return rules

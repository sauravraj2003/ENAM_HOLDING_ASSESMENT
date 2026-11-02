"""Disk cache + small JSON state. Everything expensive (scrapes, PDF text, LLM
calls) goes through here so a repeat request costs nothing."""

import functools
import hashlib
import json
import os
import tempfile
import threading
import time

from config import CACHE_DIR, DEFAULT_RULES

_lock = threading.Lock()
_flight_guard = threading.Lock()
_flights = {}


class _Flight:
    def __init__(self):
        self.lock = threading.Lock()
        self.users = 0


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
    # Requests can populate the same cache entry concurrently. A fixed `.tmp`
    # name lets one writer replace the file while another still expects it to
    # exist, producing ENOENT on Render. Give each writer its own temp file in
    # the destination directory so os.replace remains atomic on the same FS.
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(p) + ".", suffix=".tmp", dir=os.path.dirname(p))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, default=str)
        os.replace(tmp, p)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def cached(ns: str, ttl: float | None = None):
    """Memoise a function's JSON-serialisable result on disk, keyed by its args."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, refresh: bool = False, **kwargs):
            key = json.dumps([fn.__name__, args, kwargs], sort_keys=True, default=str)
            if refresh:
                hit = None
            else:
                hit = get(ns, key, ttl)
            if hit is not None:
                return hit

            flight_key = ns + ":" + hashlib.sha256(key.encode("utf-8")).hexdigest()
            with _flight_guard:
                flight = _flights.setdefault(flight_key, _Flight())
                flight.users += 1
            try:
                with flight.lock:
                    # Another request may have filled this cache while this
                    # one waited. Recheck to avoid duplicate upstream calls.
                    if not refresh:
                        hit = get(ns, key, ttl)
                        if hit is not None:
                            return hit
                    val = fn(*args, **kwargs)
                    if val is not None:
                        put(ns, key, val)
                    return val
            finally:
                with _flight_guard:
                    flight.users -= 1
                    if flight.users == 0:
                        _flights.pop(flight_key, None)

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

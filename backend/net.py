"""Shared HTTP session with polite rate limiting for screener.in / BSE."""

import threading
import time

import requests

from config import USER_AGENT

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"})

_host_lock = threading.Lock()
_last_hit: dict[str, float] = {}
_MIN_GAP = {"www.screener.in": 0.35}


def _throttle(url: str) -> None:
    host = url.split("/")[2]
    gap = _MIN_GAP.get(host, 0)
    if not gap:
        return
    with _host_lock:
        wait = _last_hit.get(host, 0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()


def get(url: str, *, timeout: float = 30, retries: int = 2, **headers) -> requests.Response:
    hdrs = dict(headers)
    if "bseindia.com" in url:
        hdrs.setdefault("Referer", "https://www.bseindia.com/")
    last_exc = None
    for attempt in range(retries + 1):
        _throttle(url)
        try:
            r = _session.get(url, headers=hdrs, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_exc = e
            time.sleep(1 + attempt)
    raise last_exc or RuntimeError(f"GET failed: {url}")

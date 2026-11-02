"""Push side: the windowed digest, threshold alerts, and a background watcher
that streams new alerts to connected phones (Server-Sent Events)."""

import json
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import prices
import screener
import store
import taxonomy
from config import PORTFOLIO

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
SYMBOLS = tuple(p["symbol"] for p in PORTFOLIO)
SECTOR = {p["symbol"]: p["sector"] for p in PORTFOLIO}

# Rough reading-time model for the "time saved" line: opening and skimming a
# filing PDF ~4 min; reading FINN's card ~30s; a glance row ~10s.
MIN_PER_FILING, MIN_PER_CARD, MIN_PER_ROW = 4.0, 0.5, 0.17


def _parse(d: str):
    try:
        return datetime.fromisoformat(d)
    except (TypeError, ValueError):
        return None


def _ts(d):
    p = _parse(d)
    return p.timestamp() if p else 0


def _fetch_all(symbols=SYMBOLS):
    def one(sym):
        try:
            return sym, screener.announcements(sym), None
        except Exception as e:
            log.warning("announcements failed for %s: %s", sym, e)
            return sym, [], str(e)

    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(one, symbols))


def market_snapshots(lookback: int):
    try:
        hist = prices.history(SYMBOLS)
    except Exception as e:
        log.warning("price fetch failed: %s", e)
        hist = {}
    return {s: prices.snapshot(b, lookback) for s, b in hist.items()}


def price_signals(snaps: dict, rules: dict):
    out = []
    for sym, s in snaps.items():
        if not s:
            continue
        reasons = []
        if s.get("vol_multiple") and s["vol_multiple"] >= rules["volume_multiple"]:
            reasons.append("volume")
        if abs(s.get("change_pct") or 0) >= rules["price_move_pct"]:
            reasons.append("price")
        if reasons:
            out.append({
                "id": f"{sym}:{s['date']}:{'+'.join(reasons)}",
                "kind": "signal", "symbol": sym, "sector": SECTOR.get(sym), "reasons": reasons,
                "date": s["date"], "snap": s,
                "headline": _signal_line(sym, s, reasons, rules),
                "weight": 75 + min(25, abs(s.get("change_pct") or 0) * 2 + (s.get("vol_multiple") or 0) * 3),
            })
    return out


def _signal_line(sym, s, reasons, rules):
    bits = []
    if "price" in reasons:
        bits.append(f"{'up' if s['change_pct'] > 0 else 'down'} {abs(s['change_pct']):.1f}% on the day")
    if "volume" in reasons:
        bits.append(f"volume {s['vol_multiple']:.1f}x its {rules['volume_lookback']}-day average")
    return f"{sym} " + " · ".join(bits)


def digest(hours: float | None = None, rules: dict | None = None):
    rules = rules or store.load_rules()
    hours = hours or rules["window_hours"]
    now = datetime.now(IST)
    since = now - timedelta(hours=hours)
    # Price history and announcements come from independent upstreams. Fetch
    # them concurrently so the first brief waits for the slower source rather
    # than adding both network delays together.
    with ThreadPoolExecutor(max_workers=2) as ex:
        prices_future = ex.submit(market_snapshots, rules["volume_lookback"])
        announcements_future = ex.submit(_fetch_all)
        snaps = prices_future.result()
        fetched = announcements_future.result()
    lanes = {1: [], 2: [], 3: []}
    coverage_gaps = []
    total = 0
    for sym, items, err in fetched:
        if err:
            coverage_gaps.append({"symbol": sym, "reason": "fetch failed"})
            continue
        dates = [d for d in (_parse(i["date"]) for i in items) if d]
        if dates and len(items) >= 5 and min(dates) > since:
            # We only see the latest few filings per company; flag if the window reaches past them.
            coverage_gaps.append({"symbol": sym, "reason": f"only filings since {min(dates):%d %b} available"})
        for it in items:
            d = _parse(it["date"])
            if not d or d < since:
                continue
            c = taxonomy.classify(it)
            c.update({"symbol": sym, "sector": SECTOR.get(sym), "subject": taxonomy.subject(it["title"]),
                      "id": it["url"], "kind": "filing", "material": c["type"] in rules["material_types"] and c["lane"] == 1})
            if c["lane"] == 1 and c["type"] not in rules["material_types"]:
                c["lane"] = 2  # investor chose not to care about this type
            lanes[c["lane"]].append(c)
            total += 1

    signals = [s for s in price_signals(snaps, rules) if _parse(s["date"] + "T15:30:00+05:30") and
               _parse(s["date"] + "T15:30:00+05:30") >= since - timedelta(hours=18)]

    # A price/volume trigger is only useful with a "why": attach that stock's
    # filings from the same session, or say plainly that there were none.
    by_sym = {}
    for it in lanes[1] + lanes[2]:
        by_sym.setdefault(it["symbol"], []).append(it)
    for s in signals:
        day = datetime.fromisoformat(s["date"]).date()
        ctx = [f for f in by_sym.get(s["symbol"], []) if abs((_parse(f["date"]).date() - day).days) <= 3]
        s["context"] = [{"label": f["label"], "summary": f.get("summary") or f["subject"], "url": f["url"]} for f in ctx[:3]]
        s["why"] = (f"Filed around the move: {ctx[0]['label'].lower()}" if ctx else "No company filing near this move - likely market / sector flow or news.")

    # Stack related items: a results filing, its deck, transcript and press
    # release are one story, so the extras are folded under the lead card.
    needs = sorted(lanes[1] + signals, key=lambda x: (-x["weight"], -_ts(x.get("date"))))
    lead_by_symbol = {}
    for it in needs:
        if it.get("type") == "results":
            lead_by_symbol.setdefault(it["symbol"], it)
    glance = []
    for it in sorted(lanes[2], key=lambda x: (-x["weight"], -_ts(x["date"]))):
        lead = lead_by_symbol.get(it["symbol"])
        if lead and lead.get("type") == "results" and it["type"] in ("deck", "transcript", "press", "concall_outcome", "board"):
            lead.setdefault("related", []).append({k: it[k] for k in ("subject", "summary", "url", "date", "label")})
        else:
            glance.append(it)
    routine = {}
    for it in lanes[3]:
        routine.setdefault(it["label"], []).append(it)
    routine_groups = sorted(({"label": k, "count": len(v), "items": v} for k, v in routine.items()), key=lambda g: -g["count"])

    shown_cards = len(needs)
    minutes_saved = max(0, round(total * MIN_PER_FILING - shown_cards * MIN_PER_CARD - len(glance) * MIN_PER_ROW))
    movers = sorted((dict(symbol=s, **{k: v for k, v in sn.items() if k in ("change_pct", "close", "vol_multiple", "spark")})
                     for s, sn in snaps.items() if sn), key=lambda x: -abs(x["change_pct"]))
    return {
        "window_hours": hours, "since": since.isoformat(), "generated_at": now.isoformat(),
        "counts": {"filings": total, "needs_you": len(lanes[1]), "signals": len(signals), "glance": len(glance), "routine": len(lanes[3])},
        "minutes_saved": minutes_saved,
        "needs_you": needs, "glance": glance, "routine": routine_groups,
        "movers": movers[:6], "coverage_gaps": coverage_gaps,
        "price_as_of": next((s["date"] for s in snaps.values() if s), None),
    }


# ── Alerts + watcher ──────────────────────────────────────────────────────────

_subscribers: list[queue.Queue] = []
_sub_lock = threading.Lock()
_seen_path_key = "seen-alert-ids"
_watcher_lock = threading.Lock()
_watcher_thread = None


def alerts(rules: dict | None = None):
    rules = rules or store.load_rules()
    d = digest(max(rules["window_hours"], 72), rules)
    out = [i for i in d["needs_you"] if i["kind"] == "signal" or i.get("material")]
    return sorted(out, key=lambda x: x.get("date") or "", reverse=True)


def subscribe() -> queue.Queue:
    q = queue.Queue(maxsize=50)
    with _sub_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    with _sub_lock:
        if q in _subscribers:
            _subscribers.remove(q)


def broadcast(event: dict):
    msg = json.dumps(event, default=str)
    with _sub_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


def _watch_loop():
    # Let the first user-triggered request populate shared caches before the
    # background watcher starts the same portfolio scrape on a fresh worker.
    time.sleep(30)
    seen = set(store.get("state", _seen_path_key) or [])
    first = not seen
    while True:
        rules = store.load_rules()
        try:
            current = alerts(rules)
            fresh = [a for a in current if a["id"] not in seen]
            if fresh and not first:
                for a in fresh:
                    broadcast({"type": "alert", "item": _slim(a)})
            seen |= {a["id"] for a in current}
            store.put("state", _seen_path_key, sorted(seen))
            first = False
        except Exception as e:
            log.warning("watcher pass failed: %s", e)
        time.sleep(max(60, rules["poll_minutes"] * 60))


def _slim(a):
    return {k: a.get(k) for k in ("id", "kind", "symbol", "label", "subject", "summary", "headline", "date", "url", "type")}


def start_watcher():
    global _watcher_thread
    with _watcher_lock:
        if _watcher_thread and _watcher_thread.is_alive():
            return
        _watcher_thread = threading.Thread(target=_watch_loop, name="finn-watcher", daemon=True)
        _watcher_thread.start()

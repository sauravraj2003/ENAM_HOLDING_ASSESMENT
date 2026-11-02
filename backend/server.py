"""FINN API + static frontend. Run: python server.py  ->  http://localhost:8000"""

import logging
import os
import queue
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, Response, jsonify, request, send_from_directory

import enrich
import llm
import prices
import push
import research
import screener
import store
from config import PORT, PORTFOLIO, RESEARCH_DEMO

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
FRONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
app = Flask(__name__, static_folder=None)
# Start the in-process alert watcher for both `python server.py` and WSGI hosts.
push.start_watcher()


def _err(e, code=500):
    logging.exception("request failed")
    return jsonify({"error": str(e)}), code


@app.get("/")
def index():
    return send_from_directory(FRONT, "index.html")


@app.get("/<path:p>")
def static_files(p):
    return send_from_directory(FRONT, p)


@app.get("/api/meta")
def meta():
    return jsonify({"portfolio": PORTFOLIO, "research_demo": RESEARCH_DEMO, "llm": llm.available(),
                    "usage": llm.USAGE, "rules": store.load_rules()})


@app.get("/api/digest")
def digest():
    try:
        return jsonify(push.digest(float(request.args.get("hours", 0)) or None))
    except Exception as e:
        return _err(e)


@app.get("/api/alerts")
def alerts():
    try:
        return jsonify(push.alerts())
    except Exception as e:
        return _err(e)


@app.post("/api/enrich")
def enrich_item():
    it = request.get_json(force=True)
    try:
        return jsonify(enrich.enrich(it["symbol"], it))
    except Exception as e:
        return _err(e)


@app.get("/api/rules")
def get_rules():
    return jsonify(store.load_rules())


@app.post("/api/rules")
def set_rules():
    return jsonify(store.save_rules(request.get_json(force=True)))


@app.get("/api/holdings")
def holdings():
    rules = store.load_rules()
    snaps = push.market_snapshots(rules["volume_lookback"])
    return jsonify([{**p, **(snaps.get(p["symbol"]) or {})} for p in PORTFOLIO])


@app.get("/api/search")
def search():
    try:
        query = " ".join(request.args.get("q", "").split()).upper()
        return jsonify(screener.search(query))
    except Exception as e:
        return _err(e)


@app.get("/api/research/<sym>/core")
def r_core(sym):
    try:
        symbol = sym.upper()
        # The fundamentals page and Yahoo price history are independent
        # upstream requests. Run them together for a faster first dossier.
        with ThreadPoolExecutor(max_workers=2) as ex:
            core_future = ex.submit(research.core, symbol)
            price_future = ex.submit(prices.history, (symbol,), "1y")
            d = core_future.result()
            try:
                history = price_future.result()
            except Exception:
                history = {}
        research.warm(symbol)
        h = history.get(symbol)
        d["price"] = prices.snapshot(h) if h else None
        return jsonify(d)
    except Exception as e:
        return _err(e, 404)


def _llm_route(fn, sym):
    if not llm.enabled():
        return jsonify({"disabled": True, "reason": "No model configured. Add GEMINI_API_KEY or GROQ_API_KEY to backend/.env"})
    try:
        return jsonify(fn(sym.upper()))
    except llm.LLMUnavailable as e:
        return jsonify({"disabled": True, "reason": str(e)})
    except Exception as e:
        return _err(e)


@app.get("/api/research/<sym>/business")
def r_business(sym):
    return _llm_route(research.business, sym)


@app.get("/api/research/<sym>/ledger")
def r_ledger(sym):
    return _llm_route(research.ledger, sym)


@app.get("/api/research/<sym>/bullbear")
def r_bb(sym):
    return _llm_route(research.bull_bear, sym)


@app.post("/api/research/<sym>/ask")
def r_ask(sym):
    try:
        return jsonify(research.ask(sym.upper(), request.get_json(force=True).get("q", "")))
    except Exception as e:
        return _err(e)


@app.get("/api/stream")
def stream():
    q = push.subscribe()

    def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    yield f"data: {q.get(timeout=25)}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            push.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/demo/push")
def demo_push():
    """Re-send the newest alert over the stream so the push UX can be demoed on demand."""
    a = push.alerts()
    if a:
        push.broadcast({"type": "alert", "item": push._slim(a[0]), "demo": True})
    return jsonify({"sent": bool(a)})


if __name__ == "__main__":
    push.start_watcher()
    app.run(host="0.0.0.0", port=PORT, threaded=True)

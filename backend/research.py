"""Pull side: research on any NSE company, grounded only in primary sources -
reported financials, concall transcripts, investor decks and annual reports.

Pipeline (cost-aware):
  1. core       - screener tables -> fundamentals.analyze()      (no model)
  2. guidance   - per transcript: keyword-filter paragraphs that sound like
                  guidance (~80% of the text dropped), fast model lists the
                  explicit promises. One cached call per transcript, ever.
  3. ledger     - smart model gets ONLY the extracted promises + a table of
                  what was actually reported, and marks each promise
                  delivered / missed / partly / too early.
  4. bull/bear  - smart model over computed flags + ledger. Short prompt.
  5. ask        - BM25 retrieval over transcript/deck/annual-report chunks,
                  smart model answers with [S#] citations.
"""

import logging
import math
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import filings
import fundamentals
import llm
import screener
import store

log = logging.getLogger(__name__)
DAY = 86400

GROUNDING = ("Use ONLY the source material given. Do not use outside knowledge about the company, "
             "news or broker views. If the sources don't support a point, leave it out.")


# ── sources ───────────────────────────────────────────────────────────────────

def sources(c: dict) -> dict:
    calls = [x for x in c.get("concalls", []) if x.get("transcript")][:4]
    deck = next((x for x in c.get("concalls", []) if x.get("ppt")), None)
    ar = sorted([x for x in c.get("annual_reports", []) if x.get("year")], key=lambda x: -x["year"])[:1]
    return {
        "transcripts": [{"label": f"{x['month']} call", "url": x["transcript"]} for x in calls],
        "deck": {"label": f"{deck['month']} deck", "url": deck["ppt"]} if deck else None,
        "annual_report": {"label": f"Annual Report {ar[0]['year']}", "url": ar[0]["url"]} if ar else None,
    }


def core(symbol: str) -> dict:
    c = screener.company(symbol)
    a = fundamentals.analyze(c)
    return {"symbol": c["symbol"], "name": c["name"], "consolidated": c["consolidated"], "url": c["url"],
            "about": c["about"], "sources": sources(c), "llm": llm.available(), **a}


# ── 2. guidance extraction ────────────────────────────────────────────────────

_CUES = re.compile(
    r"\b(guid|expect|target|outlook|aspir|aim|plan to|planning|we will|we would|should be|going forward|"
    r"next (year|quarter|few)|fy ?2\d|fy\d\d|h2|second half|medium term|by 20\d\d|capex|margin|ebitda|growth|"
    r"double[- ]digit|high single|mid[- ]teens|teens|band|range|run[- ]rate|order book|pipeline)\b", re.I)


def _guidance_passages(url: str, budget: int = 11000) -> str:
    pgs = filings.pages(url)
    if not pgs:
        return ""
    out, used = [], 0
    for pi, page in enumerate(pgs):
        for para in re.split(r"\n\s*\n|(?<=[.?!])\s{2,}", page):
            para = re.sub(r"\s+", " ", para).strip()
            if len(para) < 80:
                continue
            hits = len(_CUES.findall(para))
            has_num = bool(re.search(r"\d+\s?(%|percent|bps|crore|cr\b|billion|mn|million)", para, re.I))
            if hits >= 2 and has_num:
                chunk = f"[p{pi + 1}] {para[:900]}"
                out.append(chunk)
                used += len(chunk)
                if used > budget:
                    return "\n".join(out)
    return "\n".join(out)


_GUIDE_PROMPT = """These are excerpts from {company}'s earnings-call transcript ({label}).
List management's EXPLICIT forward-looking commitments on headline numbers: revenue / volume growth, margins (EBITDA/operating), capex, order book, market share, specific targets with a timeframe.
Skip vague aspirations with no number or direction. Skip analysts' questions - only management's statements.

Return JSON only: {{"items": [{{"metric": "revenue growth|margin|capex|volume|order book|other", "promise": "<=25 words, management's claim in plain English", "target": "the number/range e.g. '15-17%'", "horizon": "e.g. FY27, next quarter, medium term", "quote": "<=20 word verbatim fragment", "page": "p12"}}]}}
Max 8 items, most specific first.

EXCERPTS:
{text}"""


@store.cached("guidance")
def guidance_for(symbol: str, url: str, label: str, company: str):
    text = _guidance_passages(url)
    if not text:
        return {"label": label, "url": url, "items": []}
    d = llm.complete_json(_GUIDE_PROMPT.format(company=company, label=label, text=text), tier="fast", max_tokens=1200)
    items = d.get("items", []) if isinstance(d, dict) else []
    return {"label": label, "url": url, "items": items[:8]}


def guidance(symbol: str) -> list:
    c = screener.company(symbol)
    src = sources(c)["transcripts"]
    with ThreadPoolExecutor(max_workers=2) as ex:
        res = list(ex.map(lambda s: _safe(guidance_for, symbol, s["url"], s["label"], c["name"]), src))
    return [r for r in res if r]


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        log.warning("%s failed: %s", fn.__name__, e)
        return None


# ── 3. promise ledger: narrative vs numbers ──────────────────────────────────

def _actuals_table(c: dict) -> str:
    q = c.get("quarters")
    a = c.get("annual")
    lines = []
    if q:
        rows = q["rows"]
        sales = rows.get("Sales") or rows.get("Revenue") or []
        opm = rows.get("OPM %") or rows.get("Financing Margin %") or []
        pat = rows.get("Net Profit") or []
        lines.append("QUARTERLY (₹ cr): quarter | revenue | revenue YoY% | OPM% | PAT | PAT YoY%")
        n = len(q["cols"])
        for i in range(max(0, n - 8), n):
            ry = round((sales[i] / sales[i - 4] - 1) * 100, 1) if i >= 4 and sales[i] and sales[i - 4] else None
            py = round((pat[i] / pat[i - 4] - 1) * 100, 1) if i >= 4 and pat[i] and pat[i - 4] and pat[i - 4] > 0 else None
            lines.append(f"{q['cols'][i]} | {sales[i]} | {ry} | {opm[i] if i < len(opm) else None} | {pat[i]} | {py}")
    if a:
        rows = a["rows"]
        sales = rows.get("Sales") or rows.get("Revenue") or []
        opm = rows.get("OPM %") or rows.get("Financing Margin %") or []
        lines.append("ANNUAL (₹ cr): year | revenue | OPM% | PAT")
        for i in range(max(0, len(a["cols"]) - 4), len(a["cols"])):
            lines.append(f"{a['cols'][i]} | {sales[i] if i < len(sales) else None} | {opm[i] if i < len(opm) else None} | {(rows.get('Net Profit') or [None] * 99)[i]}")
    cf = c.get("cashflow")
    if cf and cf["rows"].get("Cash from Investing Activity"):
        lines.append("CASH FROM INVESTING (capex proxy, ₹ cr): " + ", ".join(
            f"{col} {v}" for col, v in list(zip(cf["cols"], cf["rows"]["Cash from Investing Activity"]))[-3:]))
    return "\n".join(lines)


_LEDGER_PROMPT = """You are auditing management credibility for {company}. Fiscal year ends in March (FY27 = Apr 2026-Mar 2027; Q2 FY27 = quarter ending Sep 2026).

WHAT MANAGEMENT SAID (from the last four earnings calls, oldest first):
{promises}

WHAT WAS ACTUALLY REPORTED:
{actuals}

Compare promises with reported numbers. Return JSON only:
{{"ledger": [{{"said_in": "call label", "promise": "<=20 words", "target": "", "actual": "what the numbers show, with the figure", "verdict": "delivered|missed|partly|too early|can't verify", "why": "<=18 words"}}],
 "divergences": [{{"story": "what management keeps saying", "numbers": "what the reported figures show", "agree": true}}],
 "credibility": 1-5, "credibility_note": "<=30 words",
 "guidance_now": [{{"metric": "", "target": "", "horizon": ""}}]}}
Rules: at most 8 ledger rows, prefer promises whose timeframe has passed. 'too early' if the period hasn't been reported yet. 'can't verify' if the metric isn't in the reported table (e.g. segment volumes). Max 4 divergences. guidance_now = the latest explicit guidance still open. {grounding}"""


@store.cached("ledger", ttl=7 * DAY)
def ledger(symbol: str):
    c = screener.company(symbol)
    g = guidance(symbol)
    g_sorted = list(reversed(g))  # oldest first
    promises = []
    for call in g_sorted:
        for it in call["items"]:
            promises.append(f"- [{call['label']}] {it.get('metric')}: {it.get('promise')} (target {it.get('target')}, horizon {it.get('horizon')}) \"{it.get('quote', '')}\"")
    if not promises:
        return {"ledger": [], "divergences": [], "credibility": None, "credibility_note": "No explicit numeric guidance found in recent transcripts.",
                "guidance_now": [], "calls": g}
    d = llm.complete_json(_LEDGER_PROMPT.format(company=c["name"], promises="\n".join(promises)[:9000],
                                                actuals=_actuals_table(c), grounding=GROUNDING),
                          tier="smart", max_tokens=2000)
    d["calls"] = g
    return d


# ── 4. business snapshot + bull/bear ──────────────────────────────────────────

_BIZ_PROMPT = """From these primary-source excerpts for {company} (annual report / investor deck / earnings call), describe the business.
Return JSON only: {{"one_liner": "<=20 words, what the company does", "segments": [{{"name": "", "share": "% of revenue or profit if stated, else ''", "note": "<=12 words"}}], "drivers": ["<=12 words each, up to 3 things that move the numbers"], "cites": ["source labels used"]}}
Max 6 segments. {grounding}

{text}"""


@store.cached("business", ttl=30 * DAY)
def business(symbol: str):
    c = screener.company(symbol)
    src = sources(c)
    parts = []
    kw = ["segment", "revenue", "business", "portfolio", "mix", "contribution", "share of", "%"]
    if src["deck"]:
        t = filings.pick_pages(src["deck"]["url"], kw, limit=3, max_pages=30)
        if t:
            parts.append(f"=== {src['deck']['label']} ===\n{t[:5000]}")
    if src["annual_report"]:
        t = filings.pick_pages(src["annual_report"]["url"], kw + ["overview", "who we are"], limit=3, max_pages=60)
        if t:
            parts.append(f"=== {src['annual_report']['label']} ===\n{t[:6000]}")
    if src["transcripts"]:
        pgs = filings.pages(src["transcripts"][0]["url"])
        parts.append(f"=== {src['transcripts'][0]['label']} (opening remarks) ===\n" + "\n".join(pgs[1:4])[:4000])
    if not parts:
        return {"one_liner": c["about"][:200], "segments": [], "drivers": [], "cites": ["screener profile"]}
    return llm.complete_json(_BIZ_PROMPT.format(company=c["name"], grounding=GROUNDING, text="\n\n".join(parts)),
                             tier="fast", max_tokens=900)


_BB_PROMPT = """Write a tight bull and bear case for {company} for a professional investor.
Evidence available (all computed from reported financials and the company's own earnings calls):

COMPUTED FLAGS:
{flags}

KEY NUMBERS: {numbers}

MANAGEMENT TRACK RECORD: credibility {cred}/5 - {cred_note}
OPEN GUIDANCE: {open_g}
LEDGER: {ledger}

Return JSON only: {{"bull": [{{"point": "<=16 words", "evidence": "<=14 words, the number behind it"}}], "bear": [same], "watch": ["<=14 words: the 2 things that would decide which case wins"]}}
3 bull, 3 bear. Every point must cite a number from the evidence. {grounding}"""


@store.cached("bullbear", ttl=7 * DAY)
def bull_bear(symbol: str):
    c = screener.company(symbol)
    a = fundamentals.analyze(c)
    try:
        L = ledger(symbol)
    except Exception as e:
        log.warning("ledger for bull/bear failed: %s", e)
        L = {}
    s = a["snapshot"]
    numbers = {k: s.get(k) for k in ("revenue_ttm", "pat_ttm", "opm_fy", "roe", "roce", "pe", "debt_to_equity", "q_rev_yoy", "q_pat_yoy")}
    numbers["cagr"] = a["trajectory"]["cagr"]
    numbers["cfo_to_pat"] = a["cash"]["cfo_to_pat_5y"] or a["cash"]["cfo_to_pat_3y"]
    led = "; ".join(f"{r.get('promise')} -> {r.get('verdict')}" for r in (L.get("ledger") or [])[:6])
    return llm.complete_json(_BB_PROMPT.format(
        company=c["name"], flags="\n".join(f"- {f['text']}" for f in a["flags"]) or "none",
        numbers=numbers, cred=L.get("credibility"), cred_note=L.get("credibility_note", ""),
        open_g=L.get("guidance_now"), ledger=led or "n/a", grounding=GROUNDING), tier="smart", max_tokens=900)


# ── 5. ask: BM25 over primary documents ──────────────────────────────────────

_TOK = re.compile(r"[a-z0-9%]+")
_STOP = set("the a an of to and in for on is are was were be by with that this it as at from we our you have has or but not".split())


def _tokens(s):
    return [t for t in _TOK.findall(s.lower()) if t not in _STOP and len(t) > 1]


@store.cached("chunks", ttl=7 * DAY)
def _chunks(symbol: str, include_ar: bool):
    c = screener.company(symbol)
    src = sources(c)
    docs = [(t["label"], t["url"], None) for t in src["transcripts"]]
    if src["deck"]:
        docs.append((src["deck"]["label"], src["deck"]["url"], 40))
    if include_ar and src["annual_report"]:
        docs.append((src["annual_report"]["label"], src["annual_report"]["url"], 60))
    out = []
    for label, url, mp in docs:
        try:
            pgs = filings.pages(url, mp)
        except Exception:
            continue
        for pi, page in enumerate(pgs):
            words = page.split()
            for k in range(0, len(words), 220):
                piece = " ".join(words[k:k + 260])
                if len(piece) > 200:
                    out.append({"doc": label, "page": pi + 1, "url": url, "text": piece})
    return out


def _bm25(chunks, query, k=6):
    q = _tokens(query)
    docs = [_tokens(c["text"]) for c in chunks]
    if not docs:
        return []
    N = len(docs)
    avg = sum(map(len, docs)) / N
    df = Counter(t for d in docs for t in set(d))
    scores = []
    for i, d in enumerate(docs):
        tf = Counter(d)
        s = 0.0
        for t in q:
            if t in tf:
                idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        scores.append((s, i))
    scores.sort(reverse=True)
    return [chunks[i] for s, i in scores[:k] if s > 0]


_ASK_PROMPT = """Question about {company}: {q}

Sources (primary documents only):
{ctx}

Answer in <=120 words, investor-grade, numbers first. Cite like [S1]. If the sources don't answer it, say exactly what is missing. {grounding}"""


def ask(symbol: str, question: str) -> dict:
    c = screener.company(symbol)
    ar_ready = _ar_ready(c)
    hits = _bm25(_chunks(symbol, ar_ready), question)
    if not hits:
        return {"answer": "Nothing in the recent transcripts or deck addresses that.", "sources": []}
    ctx = "\n\n".join(f"[S{i + 1}] {h['doc']} p{h['page']}: {h['text'][:1400]}" for i, h in enumerate(hits))
    if not llm.enabled():
        return {"answer": None, "sources": [{**h, "text": h["text"][:400]} for h in hits], "note": "No model configured - showing the most relevant passages."}
    ans = llm.complete(_ASK_PROMPT.format(company=c["name"], q=question, ctx=ctx, grounding=GROUNDING), tier="smart", max_tokens=500)
    return {"answer": ans, "sources": [{**h, "text": h["text"][:400]} for h in hits], "annual_report_indexed": ar_ready}


_ar_jobs: set = set()


def _ar_ready(c) -> bool:
    """Annual reports are 10-30 MB; read them in the background the first time."""
    ar = sources(c)["annual_report"]
    if not ar:
        return False
    key = __import__("json").dumps(["pages", [ar["url"], 60], {}], sort_keys=True)
    if store.get("pdftext", key) is not None:
        return True
    if ar["url"] not in _ar_jobs:
        _ar_jobs.add(ar["url"])
        threading.Thread(target=_safe, args=(filings.pages, ar["url"], 60), daemon=True).start()
    return False


def warm(symbol: str):
    """Kick off the slow, cacheable work so the dossier fills in fast."""
    c = screener.company(symbol)
    _ar_ready(c)
    if llm.enabled():
        threading.Thread(target=_safe, args=(ledger, symbol), daemon=True).start()

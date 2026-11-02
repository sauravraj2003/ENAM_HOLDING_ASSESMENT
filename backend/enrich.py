"""Turn a material filing into something an investor can read in 5 seconds.

  results  -> Revenue / EBITDA / PBT / PAT, QoQ and YoY, read from the results
              filing itself (fast model extracts line items, Python does the
              arithmetic), cross-checked against screener's quarterly table.
  mgmt     -> who is coming / going, and who they are. Reg-30 appointment
              disclosures carry a 'brief profile' annexure, so the bio comes
              from the company's own filing; a LinkedIn search link is added.
  other    -> a 2-line brief with the key numbers (deal size, order value...).
"""

import logging
import re
from datetime import datetime
from urllib.parse import quote_plus

import filings
import llm
import screener
import store

log = logging.getLogger(__name__)

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round((a / b - 1) * 100 * (1 if b > 0 else -1), 1)


def _quarter_from_text(text: str):
    m = re.search(r"(?:quarter|period|half year|year)[^.]{0,40}?ended\s+(?:on\s+)?(\w+)\s+(\d{1,2}),?\s+(\d{4})", text, re.I) \
        or re.search(r"ended\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\w+),?\s+(\d{4})", text, re.I)
    if not m:
        return None
    a, b, y = m.groups()
    mon = a if not a.isdigit() else b
    mi = MONTHS.get(mon[:3].lower())
    return f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][mi - 1]} {y}" if mi else None


def _screener_results(c: dict) -> dict | None:
    q = c.get("quarters")
    if not q or len(q["cols"]) < 2:
        return None
    rows, cols = q["rows"], q["cols"]
    is_lender = "Financing Profit" in rows
    pick = {
        "Revenue": rows.get("Sales") or rows.get("Revenue"),
        "EBITDA": None if is_lender else rows.get("Operating Profit"),
        "PBT": rows.get("Profit before tax"),
        "PAT": rows.get("Net Profit"),
    }
    n = len(cols)
    out = []
    for k, series in pick.items():
        if not series:
            out.append({"metric": k, "cur": None, "note": "not meaningful for lenders" if is_lender and k == "EBITDA" else "n/a"})
            continue
        cur, prev = series[-1], series[-2]
        yoy = series[-5] if n >= 5 else None
        out.append({"metric": k, "cur": cur, "prev": prev, "yoy": yoy, "qoq_pct": _pct(cur, prev), "yoy_pct": _pct(cur, yoy)})
    opm = rows.get("OPM %")
    return {
        "period": cols[-1], "prev_period": cols[-2], "yoy_period": cols[-5] if n >= 5 else None,
        "unit": "₹ cr", "basis": "consolidated" if c["consolidated"] else "standalone",
        "metrics": out, "margin": {"cur": opm[-1], "prev": opm[-2], "yoy": opm[-5] if n >= 5 else None} if opm else None,
        "lender": is_lender, "source": "screener.in quarterly table (compiled from exchange filings)",
        "filing_url": (q.get("pdf") or {}).get(cols[-1]),
    }


_RESULTS_PROMPT = """Below are pages from an Indian listed company's quarterly results filing.
Extract the {basis} statement of profit and loss (if only one basis exists, use that).
Columns are usually: quarter ended (current) | quarter ended (previous quarter) | quarter ended (same quarter last year) | half-year / year columns. Ignore half-year and full-year columns.

Return JSON only:
{{"basis": "consolidated|standalone", "unit": "crore|lakh|million|thousand", "periods": {{"cur": "Sep 2026", "prev": "Jun 2026", "yoy": "Sep 2025"}},
 "revenue": [cur, prev, yoy], "other_income": [..], "finance_cost": [..], "depreciation": [..], "pbt": [..], "pat": [..]}}
- revenue = Revenue from operations (not total income)
- pbt = profit before tax (after exceptional items)
- pat = net profit for the period (total, before OCI)
- numbers as plain numbers, negative for losses, null if absent. Do not compute anything.

PAGES:
{pages}"""

_UNIT = {"crore": 1, "lakh": 0.01, "lakhs": 0.01, "million": 0.1, "thousand": 0.0001, "mn": 0.1}


def _filing_results(url: str) -> dict | None:
    if not llm.enabled() or not url:
        return None
    pages = filings.pick_pages(url, ["revenue from operations", "profit before tax", "finance cost", "depreciation",
                                      "other income", "quarter ended", "consolidated"], limit=2)
    if not pages:
        return None
    try:
        d = llm.complete_json(_RESULTS_PROMPT.format(basis="consolidated", pages=pages[:14000]), tier="fast", max_tokens=600)
    except Exception as e:
        log.warning("results extraction failed: %s", e)
        return None
    u = str(d.get("unit", "crore")).lower()
    mult = next((v for k, v in _UNIT.items() if k in u), 1)

    def col(key, i):
        try:
            v = d.get(key, [None] * 3)[i]
            return round(float(v) * mult, 2) if v is not None else None
        except (TypeError, ValueError, IndexError):
            return None

    def ebitda(i):
        p, f, dp, oi = col("pbt", i), col("finance_cost", i), col("depreciation", i), col("other_income", i)
        if None in (p, f, dp):
            return None
        return round(p + f + dp - (oi or 0), 2)

    metrics = []
    for name, getter in (("Revenue", lambda i: col("revenue", i)), ("EBITDA", ebitda),
                         ("PBT", lambda i: col("pbt", i)), ("PAT", lambda i: col("pat", i))):
        cur, prev, yoy = getter(0), getter(1), getter(2)
        metrics.append({"metric": name, "cur": cur, "prev": prev, "yoy": yoy, "qoq_pct": _pct(cur, prev), "yoy_pct": _pct(cur, yoy)})
    p = d.get("periods") or {}
    return {"period": p.get("cur"), "prev_period": p.get("prev"), "yoy_period": p.get("yoy"), "unit": "₹ cr",
            "basis": d.get("basis", "consolidated"), "metrics": metrics,
            "source": "results filing (line items extracted by model, EBITDA = PBT + finance cost + D&A - other income)",
            "filing_url": url}


@store.cached("enrich-results", ttl=3 * 3600)
def results_card(symbol: str, url: str | None = None, title: str = ""):
    c = screener.company(symbol)
    base = _screener_results(c)
    filing_q = _quarter_from_text(title) if title else None
    from_filing = _filing_results(url) if url else None
    card = from_filing or base
    if not card:
        return None
    card = dict(card)
    if from_filing and base and base["period"] == from_filing.get("period"):
        # Fill any column the model skipped from the reported table for the same quarter.
        b = {m["metric"]: m for m in base["metrics"]}
        for m in card["metrics"]:
            ref = b.get(m["metric"]) or {}
            for k in ("cur", "prev", "yoy"):
                if m.get(k) is None and ref.get(k) is not None:
                    m[k] = ref[k]
            m["qoq_pct"], m["yoy_pct"] = _pct(m.get("cur"), m.get("prev")), _pct(m.get("cur"), m.get("yoy"))
        card["yoy_period"] = card.get("yoy_period") or base.get("yoy_period")
    # Cross-check: same quarter in both sources -> show agreement.
    if from_filing and base and base["period"] == (from_filing.get("period") or filing_q):
        b = {m["metric"]: m["cur"] for m in base["metrics"]}
        diffs = []
        for m in from_filing["metrics"]:
            if m["cur"] and b.get(m["metric"]):
                diffs.append(abs(m["cur"] / b[m["metric"]] - 1))
        card["crosscheck"] = "matches screener" if diffs and max(diffs) < 0.03 else "differs from screener - check basis"
    if not from_filing and base and filing_q and filing_q != base["period"]:
        card["stale"] = f"screener still shows {base['period']}; filing is for {filing_q}"
    if base and base.get("margin"):
        card["margin"] = base["margin"]
    card["lender"] = bool(base and base.get("lender"))
    return card


_MGMT_PROMPT = """This is a stock-exchange disclosure by an Indian listed company about a change in directors / key managerial personnel / senior management.
Return JSON only: {{"people": [{{"name": "", "role": "", "change": "appointed|resigned|ceased|re-appointed|redesignated|retired", "effective": "", "reason": "", "bio": "", "prior": [""], "education": ""}}]}}
- bio: 1-2 sentences built ONLY from the brief-profile / background text in the filing (experience, previous employers, years). Empty string if the filing has none.
- prior: up to 4 previous organisations/roles named in the filing.
- reason: stated reason for resignation if any, quoted briefly.
Keep it factual. No outside knowledge.

FILING:
{text}"""

_NAME = re.compile(r"\b(?:Mr|Ms|Mrs|Dr|Shri|Smt)\.?\s+([A-Z][a-zA-Z.]+(?:\s+[A-Z][a-zA-Z.]+){0,3})")


_PROSE = re.compile(r"\b(experience|years|served|graduate|degree|joined|worked|career|holds|qualified|chartered)\b", re.I)


def _profile_sentences(txt: str) -> str:
    """Pull 1-2 biographical sentences from the filing without a model."""
    flat = re.sub(r"\s+", " ", txt)
    sents = re.split(r"(?<=[.;])\s+", flat)
    good = [s for s in sents if 60 < len(s) < 400 and len(_PROSE.findall(s)) >= 1 and "Regulation" not in s and "SEBI" not in s]
    return " ".join(good[:2])


@store.cached("enrich-mgmt", ttl=6 * 3600)
def mgmt_card(symbol: str, url: str, title: str = "", summary: str = ""):
    txt = filings.text(url, max_pages=8)
    people = []
    if llm.enabled() and txt:
        try:
            people = llm.complete_json(_MGMT_PROMPT.format(text=txt[:12000]), tier="fast", max_tokens=900).get("people", [])
        except Exception as e:
            log.warning("mgmt extraction failed: %s", e)
    if not people:
        # Deterministic fallback: named people + any biographical sentences in the filing.
        hay = f"{title}\n{summary}\n{txt}"
        names = [n for n in dict.fromkeys(_NAME.findall(hay)) if len(n) > 4][:2]
        change = ("retired" if re.search(r"retire", hay, re.I) else "resigned" if re.search(r"resign|cessation|ceas", hay, re.I)
                  else "appointed")
        bio = _profile_sentences(txt)
        people = [{"name": n, "role": "", "change": change, "bio": bio if i == 0 else "",
                   "prior": [], "effective": "", "reason": ""} for i, n in enumerate(names)]
    company = screener.company(symbol)["name"]
    for p in people:
        p["linkedin"] = "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{p.get('name', '')} {company}")
    return {"people": people, "filing_url": url}


_BRIEF_PROMPT = """Stock-exchange filing by {company}. In JSON only:
{{"headline": "<=14 words: what happened, with the key number", "numbers": ["up to 3 key figures with units, e.g. 'Order value ₹1,240 cr'"], "so_what": "<=20 words on why a shareholder should care, strictly from the filing"}}

FILING:
{text}"""


@store.cached("enrich-brief")
def brief_card(symbol: str, url: str):
    if not llm.enabled():
        return None
    txt = filings.text(url, max_pages=4)
    if not txt:
        return None
    company = screener.company(symbol)["name"]
    try:
        return llm.complete_json(_BRIEF_PROMPT.format(company=company, text=txt[:8000]), tier="fast", max_tokens=300)
    except Exception as e:
        log.warning("brief failed: %s", e)
        return None


def enrich(symbol: str, item: dict) -> dict:
    t = item.get("type")
    if t == "results":
        return {"kind": "results", "data": results_card(symbol, item.get("url"), item.get("title", "") + " " + item.get("summary", ""))}
    if t == "mgmt":
        return {"kind": "mgmt", "data": mgmt_card(symbol, item["url"], item.get("title", ""), item.get("summary", ""))}
    return {"kind": "brief", "data": brief_card(symbol, item["url"])}

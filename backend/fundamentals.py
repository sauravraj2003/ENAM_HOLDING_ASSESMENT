"""Deterministic research: everything that is arithmetic is done in Python, not
by a model. Snapshot, trajectory, balance sheet and cash quality all come from
the reported statements, so they are instant, free and can't hallucinate."""


def _series(t, *names):
    if not t:
        return None
    for n in names:
        if n in t["rows"]:
            return t["rows"][n]
    return None


def _cagr(series, cols, years):
    """CAGR over the last `years` full fiscal years (skips a trailing TTM column)."""
    if not series:
        return None
    vals = list(zip(cols, series))
    if vals and vals[-1][0] == "TTM":
        vals = vals[:-1]
    if len(vals) <= years:
        return None
    end, start = vals[-1][1], vals[-1 - years][1]
    if not start or not end or start <= 0 or end <= 0:
        return None
    return round(((end / start) ** (1 / years) - 1) * 100, 1)


def _last(series, n=1):
    if not series:
        return None
    clean = [v for v in series if v is not None]
    return clean[-n] if len(clean) >= n else None


def _pct(a, b):
    if a is None or not b:
        return None
    return round((a / b - 1) * 100, 1)


def _full_years(t):
    cols = t["cols"] if t else []
    return [i for i, c in enumerate(cols) if c != "TTM"]


def analyze(c: dict) -> dict:
    q, a, b, cf, rt = c.get("quarters"), c.get("annual"), c.get("balance"), c.get("cashflow"), c.get("ratio_table")
    lender = bool(q and "Financing Profit" in q["rows"])
    sales_a = _series(a, "Sales", "Revenue")
    op_a = _series(a, "Operating Profit", "Financing Profit")
    pat_a = _series(a, "Net Profit")
    opm_a = _series(a, "OPM %", "Financing Margin %")
    sales_q = _series(q, "Sales", "Revenue")
    pat_q = _series(q, "Net Profit")
    opm_q = _series(q, "OPM %", "Financing Margin %")
    r = c.get("ratios", {})

    acols = a["cols"] if a else []
    fy_idx = _full_years(a)
    ttm = acols and acols[-1] == "TTM"

    # ── Financial snapshot
    equity = (_last(_series(b, "Equity Capital")) or 0) + (_last(_series(b, "Reserves")) or 0)
    debt = _last(_series(b, "Borrowings", "Borrowing"))
    snapshot = {
        "market_cap": r.get("Market Cap"), "price": r.get("Current Price"), "pe": r.get("Stock P/E"),
        "roe": r.get("ROE"), "roce": r.get("ROCE"), "div_yield": r.get("Dividend Yield"),
        "revenue_ttm": sales_a[-1] if ttm and sales_a else _last(sales_a),
        "pat_ttm": pat_a[-1] if ttm and pat_a else _last(pat_a),
        "opm_fy": opm_a[fy_idx[-1]] if opm_a and fy_idx else None,
        "debt_to_equity": round(debt / equity, 2) if debt is not None and equity and not lender else None,
        "latest_quarter": q["cols"][-1] if q else None,
        "q_rev_yoy": _pct(sales_q[-1], sales_q[-5]) if sales_q and len(sales_q) >= 5 else None,
        "q_pat_yoy": _pct(pat_q[-1], pat_q[-5]) if pat_q and len(pat_q) >= 5 else None,
    }

    # ── Trajectory
    traj = {
        "years": [acols[i] for i in fy_idx][-10:],
        "revenue": [sales_a[i] for i in fy_idx][-10:] if sales_a else [],
        "op_profit": [op_a[i] for i in fy_idx][-10:] if op_a else [],
        "pat": [pat_a[i] for i in fy_idx][-10:] if pat_a else [],
        "opm": [opm_a[i] for i in fy_idx][-10:] if opm_a else [],
        "cagr": {k: {"3y": _cagr(s, acols, 3), "5y": _cagr(s, acols, 5)} for k, s in
                 (("revenue", sales_a), ("op_profit", op_a), ("pat", pat_a))},
        "quarters": {"cols": q["cols"][-12:] if q else [], "revenue": (sales_q or [])[-12:],
                     "pat": (pat_q or [])[-12:], "opm": (opm_q or [])[-12:]},
    }

    # ── Balance sheet
    bcols = b["cols"] if b else []
    borrow = _series(b, "Borrowings", "Borrowing") or []
    eq_series = [((e or 0) + (rr or 0)) for e, rr in zip(_series(b, "Equity Capital") or [], _series(b, "Reserves") or [])]
    de = [round(x / y, 2) if x is not None and y else None for x, y in zip(borrow, eq_series)]
    cwip = _series(b, "CWIP")
    fa = _series(b, "Fixed Assets")
    balance = {
        "cols": bcols[-6:], "borrowings": borrow[-6:], "net_worth": eq_series[-6:], "debt_to_equity": de[-6:] if not lender else [],
        "deposits": (_series(b, "Deposits") or [])[-6:],
        "cwip_to_fixed": round(cwip[-1] / fa[-1] * 100, 1) if cwip and fa and fa[-1] else None,
        "wc": {k: (_series(rt, k) or [])[-6:] for k in ("Debtor Days", "Inventory Days", "Days Payable", "Cash Conversion Cycle", "Working Capital Days")},
        "wc_cols": (rt["cols"] if rt else [])[-6:],
        "lender": lender,
    }

    # ── Cash quality
    cfo = _series(cf, "Cash from Operating Activity") or []
    cfcols = cf["cols"] if cf else []
    pat_by_year = dict(zip(acols, pat_a or []))
    pairs = [(col, v, pat_by_year.get(col)) for col, v in zip(cfcols, cfo)]
    pairs = [p for p in pairs if p[1] is not None and p[2]][-6:]
    cum = lambda n: (round(sum(p[1] for p in pairs[-n:]) / sum(p[2] for p in pairs[-n:]), 2)
                     if len(pairs) >= n and sum(p[2] for p in pairs[-n:]) > 0 else None)
    conv5 = cum(5) or cum(3)
    fcf = _series(cf, "Free Cash Flow") or []
    cash = {
        "cols": [p[0] for p in pairs], "cfo": [p[1] for p in pairs], "pat": [p[2] for p in pairs],
        "cfo_to_pat_5y": cum(5), "cfo_to_pat_3y": cum(3),
        "fcf": fcf[-6:], "fcf_cols": cfcols[-6:],
        "cfo_to_ebitda": (_series(cf, "CFO/EBITDA") or [])[-6:],
        "verdict": None if conv5 is None or lender else ("strong" if conv5 >= 0.9 else "fair" if conv5 >= 0.7 else "weak"),
    }

    flags = _flags(traj, balance, cash, snapshot, lender)
    return {"lender": lender, "snapshot": snapshot, "trajectory": traj, "balance": balance, "cash": cash,
            "flags": flags, "verdict": _verdict(traj, balance, cash, snapshot, lender)}


def _trend(series, n=4):
    s = [v for v in (series or []) if v is not None][-n:]
    if len(s) < 2:
        return 0
    return s[-1] - s[0]


def _flags(traj, bal, cash, snap, lender):
    out = []
    cg = traj["cagr"]
    r5, p5 = cg["revenue"]["5y"], cg["pat"]["5y"]
    if r5 is not None and p5 is not None:
        if p5 > r5 + 5:
            out.append({"tone": "pos", "text": f"Profit compounding faster than sales (PAT {p5}% vs revenue {r5}% 5y CAGR) - operating leverage."})
        elif p5 < r5 - 5:
            out.append({"tone": "neg", "text": f"Sales growing faster than profit (revenue {r5}% vs PAT {p5}% 5y CAGR) - margins being given up."})
    r3 = cg["revenue"]["3y"]
    if r3 is not None and r5 is not None and abs(r3 - r5) >= 5:
        out.append({"tone": "pos" if r3 > r5 else "neg",
                    "text": f"Growth {'accelerating' if r3 > r5 else 'decelerating'}: revenue 3y CAGR {r3}% vs 5y {r5}%."})
    dm = _trend(traj["opm"], 4)
    if traj["opm"] and abs(dm) >= 2 and not lender:
        out.append({"tone": "pos" if dm > 0 else "neg", "text": f"Operating margin {'expanded' if dm > 0 else 'compressed'} {abs(dm):.0f} pts over the last 3 years."})
    if snap.get("q_rev_yoy") is not None and r3 is not None and abs(snap["q_rev_yoy"] - r3) > 8:
        out.append({"tone": "pos" if snap["q_rev_yoy"] > r3 else "neg",
                    "text": f"Latest quarter revenue {snap['q_rev_yoy']:+.0f}% YoY vs a {r3}% 3y trend - a break from trend."})
    de = [v for v in bal["debt_to_equity"] if v is not None]
    if de and not lender:
        if de[-1] > 1:
            out.append({"tone": "neg", "text": f"Leveraged: debt/equity {de[-1]:.2f}x."})
        elif len(de) >= 3 and de[-1] - de[-3] > 0.25:
            out.append({"tone": "neg", "text": f"Debt rising: debt/equity from {de[-3]:.2f}x to {de[-1]:.2f}x."})
        elif de[-1] < 0.1:
            out.append({"tone": "pos", "text": "Effectively debt-free balance sheet."})
    ccc = [v for v in bal["wc"].get("Cash Conversion Cycle", []) if v is not None]
    if len(ccc) >= 3 and ccc[-1] - ccc[-3] >= 15:
        out.append({"tone": "neg", "text": f"Working capital stretching: cash conversion cycle {ccc[-3]:.0f} -> {ccc[-1]:.0f} days."})
    dd = [v for v in bal["wc"].get("Debtor Days", []) if v is not None]
    if len(dd) >= 3 and dd[-1] - dd[-3] >= 15:
        out.append({"tone": "neg", "text": f"Receivables building: debtor days {dd[-3]:.0f} -> {dd[-1]:.0f}."})
    if cash["verdict"] == "weak":
        out.append({"tone": "neg", "text": f"Profits not turning into cash: CFO/PAT {cash['cfo_to_pat_5y'] or cash['cfo_to_pat_3y']}x cumulative."})
    elif cash["verdict"] == "strong":
        out.append({"tone": "pos", "text": f"High-quality earnings: operating cash flow {cash['cfo_to_pat_5y'] or cash['cfo_to_pat_3y']}x of reported profit."})
    if bal.get("cwip_to_fixed") and bal["cwip_to_fixed"] > 25:
        out.append({"tone": "neu", "text": f"Heavy build-out underway: capital WIP is {bal['cwip_to_fixed']:.0f}% of fixed assets."})
    return out


def _verdict(traj, bal, cash, snap, lender):
    chips = []
    r3 = traj["cagr"]["revenue"]["3y"]
    if r3 is not None:
        chips.append({"k": "Growth", "v": f"{r3:.0f}% 3y", "tone": "pos" if r3 >= 12 else "neu" if r3 >= 6 else "neg"})
    if not lender and traj["opm"]:
        dm = _trend(traj["opm"], 4)
        chips.append({"k": "Margins", "v": "expanding" if dm >= 2 else "compressing" if dm <= -2 else "stable",
                      "tone": "pos" if dm >= 2 else "neg" if dm <= -2 else "neu"})
    if cash["verdict"]:
        chips.append({"k": "Cash", "v": cash["verdict"], "tone": {"strong": "pos", "fair": "neu", "weak": "neg"}[cash["verdict"]]})
    de = [v for v in bal["debt_to_equity"] if v is not None]
    if de and not lender:
        chips.append({"k": "Leverage", "v": f"{de[-1]:.2f}x D/E", "tone": "pos" if de[-1] < 0.3 else "neu" if de[-1] < 1 else "neg"})
    if snap.get("roe") is not None:
        chips.append({"k": "ROE", "v": f"{snap['roe']:.0f}%", "tone": "pos" if snap["roe"] >= 18 else "neu" if snap["roe"] >= 12 else "neg"})
    return chips

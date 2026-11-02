"""Announcement taxonomy and triage.

Every filing lands in one of three lanes:
  1 'needs you'      - material: results, M&A, management change, fund raise,
                       ratings actions, legal/regulatory, large orders, capex
  2 'worth a glance' - context around material events: decks, transcripts,
                       press releases, dividends, board-meeting dates
  3 'routine'        - compliance noise, collapsed by default

Rules are ordered; first match wins. Plain keyword rules are deliberate: they
are free, instant, explainable, and BSE subjects are formulaic enough that a
model adds little here. The model is spent on enrichment instead.
"""

import re

# (type, lane, label, regex over "title | summary")
RULES = [
    ("board", 2, "Board meeting date", r"board meeting intimation|intimation of (the )?board meeting|prior intimation"),
    ("auditor", 2, "Auditor change", r"statutory auditor|secretarial auditor|\bauditors?\b"),
    ("results", 1, "Results", r"financial results|results for the quarter|quarterly results|unaudited.*results|audited.*results"),
    ("mgmt", 1, "Management change", r"change in (directors|management|key managerial|kmp|senior management)|resignation|resigns|appointment of|appointed as|cessation|re-?designat|chief (executive|financial|operating)|\bceo\b|\bcfo\b|managing director|retire|directorate"),
    ("mna", 1, "M&A / restructuring", r"acqui|amalgamat|merger|demerg|scheme of arrangement|takeover|open offer|divest|slump sale|stake (sale|purchase)|joint venture|subsidiary.*(incorporat|sale)"),
    ("fundraise", 1, "Fund raise", r"\bqip\b|qualified institution|preferential (issue|allot)|rights issue|\bncd|non-convertible|debentures|commercial paper|\bipo\b|fund ?rais|raising of funds"),
    ("rating", 1, "Credit rating", r"credit rating|rating action|\b(icra|crisil|care ratings|india ratings|brickwork)\b"),
    ("legal", 1, "Legal / regulatory", r"litigation|penalty|show cause|demand order|tax demand|\bgst\b.*order|sebi order|\bsat\b|\bnclt\b|insolvency|default in|fraud|income tax search|\braid\b|investigation|arbitration|court|tribunal"),
    ("orders", 1, "Order / contract", r"order (win|received|inflow)|bagged|bags|award(ed)? of|letter of (award|intent)|\bloa\b|\bcontract\b|receipt of order|work order"),
    ("capex", 1, "Capacity / capex", r"capacity expansion|commission|new plant|greenfield|brownfield|capex|commercial production|commencement of (operations|production)"),
    ("buyback", 1, "Buyback", r"buy-?back"),
    ("pledge", 1, "Promoter pledge", r"pledge|encumbrance"),
    ("deck", 2, "Investor presentation", r"investor presentation|earnings presentation"),
    ("transcript", 2, "Concall transcript", r"transcript"),
    ("concall_outcome", 2, "Analyst meet outcome", r"analyst.*(meet|call).*outcome|outcome of.*(analyst|investor)|audio recording|earnings call.*recording"),
    ("press", 2, "Press release", r"press release|media release"),
    ("dividend", 2, "Dividend / record date", r"dividend|record date|bonus|stock split|sub-division"),
    ("board", 2, "Board meeting", r"board meeting|meeting of the board|outcome of board"),
    ("concall_intimation", 3, "Concall scheduled", r"analyst.*(meet|call)|investor (meet|call)|earnings call|conference call"),
    ("agm", 3, "Shareholder meeting", r"\bagm\b|\begm\b|annual general|postal ballot|e-voting|scrutini"),
    ("trading_window", 3, "Trading window", r"trading window"),
    ("esop", 3, "ESOP allotment", r"esop|esos|employee stock|allotment of (equity )?shares under"),
    ("compliance", 3, "Compliance filing", r"regulation 74|reg\.? 74|regulation 39|regulation 40|certificate|compliance|newspaper|publication|duplicate share|loss of share|investor complaint|shareholding pattern|book closure|intimation"),
]
_COMPILED = [(t, lane, label, re.compile(rx, re.I)) for t, lane, label, rx in RULES]

ROUTINE = ("other", 3, "Other filing")

# Within 'needs you', not all items are equal. Base weights used for ordering.
WEIGHT = {"results": 100, "mgmt": 90, "mna": 88, "legal": 85, "fundraise": 80, "pledge": 80,
          "rating": 70, "orders": 72, "capex": 65, "buyback": 70, "press": 45, "deck": 40,
          "transcript": 42, "concall_outcome": 30, "auditor": 30, "dividend": 35, "board": 25}

SENIOR = re.compile(r"\b(ceo|cfo|coo|managing director|chief executive|chief financial|whole[- ]time director|chairman|chairperson)\b", re.I)


def classify(item: dict) -> dict:
    title = item.get("title", "")
    hay = f"{title} | {item.get('summary', '')}"
    # The subject after 'Regulation 30 (LODR)-' is the cleanest signal.
    subject = title.split("(LODR)-", 1)[-1] if "(LODR)-" in title else title
    for t, lane, label, rx in _COMPILED:
        if rx.search(subject):
            return _decorate(item, t, lane, label, hay)
    # Second pass on the body text for things like large orders announced in a press release.
    for t, lane, label, rx in _COMPILED:
        if rx.search(hay):
            return _decorate(item, t, lane, label, hay)
    return _decorate(item, *ROUTINE, hay)


def _decorate(item, t, lane, label, hay):
    w = WEIGHT.get(t, 10)
    if t == "mgmt":
        # Independent-director churn is far less interesting than a CEO/CFO move.
        if SENIOR.search(hay):
            w += 15
        elif re.search(r"independent director|company secretary|compliance officer", hay, re.I):
            lane, w = 2, 30
    if t == "auditor" and re.search(r"resign", hay, re.I):
        lane, w, label = 1, 82, "Auditor resignation"
    if t == "legal" and re.search(r"no (material )?impact|not material", hay, re.I):
        lane, w = 2, 30
    return {**item, "type": t, "lane": lane, "label": label, "weight": w}


def subject(title: str) -> str:
    s = title.split("(LODR)-", 1)[-1] if "(LODR)-" in title else title
    return s.strip(" -")

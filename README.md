# FINN: a filter for investors

FINN is a mobile-first investment agent for an 18-stock NSE portfolio. It assumes the investor's scarce resource is attention. **Push** tells you what changed and hides everything else. **Pull** researches any NSE company using only primary sources.

## Run it

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env        # add GEMINI_API_KEY (free) or GROQ_API_KEY; optional
python server.py            # http://localhost:8000 — open on your phone via your LAN IP
```

The app runs without any model key. Everything that is arithmetic (digest, alerts, results tables, fundamentals, flags) is deterministic. A key adds the results-filing extraction, the management bios, the "promised vs delivered" ledger, bull/bear and grounded Q&A.

## What's in it

| Tab | What it does |
|---|---|
| **Brief** (push) | Time-window digest (24h / 3d / week / 30d). It has three lanes: **Needs you** (material filings plus price/volume triggers, as cards), **Worth a glance** (one-liners) and **Routine** (collapsed by type). You swipe a card away when you've dealt with it. A results filing's deck, transcript and press release are stacked under the results card. A ▶ button reads the brief aloud for the commute, and the header line estimates the reading time saved. |
| Filing sheet | **Results:** Revenue, EBITDA, PBT and PAT, this quarter vs QoQ and YoY. The model only extracts line items from the filing; Python computes EBITDA (PBT + finance cost + D&A − other income) and the growth rates, then cross-checks against screener's table. Banks show "EBITDA not meaningful for lenders" instead of a fake number. **Management change:** who, what role, and their bio taken from the filing's own Reg-30 "brief profile" annexure, plus a LinkedIn link. **Other material filings:** a headline, key numbers, and why it matters. |
| **Ask** (pull) | Works on any NSE symbol or company name, by text or voice. The dossier opens with a verdict strip (growth, margins, cash, leverage, ROE). Below that: business mix, a numbers snapshot, auto-generated "what the numbers say" flags, trajectory (CAGRs and quarterly bars), balance sheet and cash quality, the **promised vs delivered ledger** across the last 4 concalls with a credibility score, bull vs bear, and "ask the filings" Q&A with page citations. |
| **Holdings** | Portfolio sorted by today's move, with sparklines, volume multiples and an open-item count for each stock. |
| **Rules** | Alert thresholds written as editable sentences ("tell me when volume > 2× its 20-day average…"), toggles for which filing types count as material, browser notifications, and a test-push button. A background watcher streams new alerts over SSE. |

## Architecture & cost decisions

- **Data:** yfinance supplies end-of-day prices for all 18 stocks in one batched call. screener.in supplies the financial tables, recent filings and document links. Filing PDFs come from BSE attachments. NSE/BSE JSON APIs return 403 from scripts, so screener's filing feed is used; it exposes about 5 recent filings per company, which the app flags when the window reaches beyond them.
- **Triage is rules, not a model:** BSE subjects are formulaic, so keyword rules are free, instant and explainable. Model spend goes to enrichment instead.
- **Model routing** (`llm.py`): a *fast* tier for bulk extraction and a *smart* tier only for reasoning over facts that have already been extracted. It works with any provider (Gemini, Groq, OpenRouter, Cerebras, Anthropic, Ollama).
- **Chunking:** transcripts are keyword-filtered down to sentences that sound like guidance and contain numbers (about 80% of the text is dropped) before the fast model sees them. The ledger prompt contains only the extracted promises and a table of actual numbers. Results extraction sends only the 2 PDF pages that hold the P&L.
- **Caching:** every scrape, PDF text and model response is cached on disk, so re-opening anything costs nothing.
- **Grounding:** pull prompts forbid outside knowledge, and every number comes from filings or computed statements.

## Deliberately left out

- Real user accounts and multi-portfolio support.
- Native mobile push (web notifications plus SSE stand in).
- Full-history filings: this would need NSE/BSE API access from a server with an Indian IP.
- Segment-level KPIs (the ledger marks these "can't verify" rather than guessing).
- Scraping LinkedIn itself: the filing's own profile annexure is more reliable and allowed.

## Next

Pull the full filing history through a proxy and NSE's API. Add a "since you last looked" diff in place of fixed windows. Add per-holding notes, so research becomes a living investment thesis that alerts are checked against.

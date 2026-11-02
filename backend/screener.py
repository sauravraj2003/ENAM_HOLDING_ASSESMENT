"""screener.in adapter: financial tables, recent filings and document links
(annual reports, concall transcripts, investor decks) for any NSE symbol."""

import re
from datetime import datetime

from bs4 import BeautifulSoup

import net
import store

BASE = "https://www.screener.in"
SIX_HOURS = 6 * 3600


def _num(s: str):
    s = (s or "").replace(",", "").replace("%", "").replace("₹", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _table(soup, section_id: str):
    tb = soup.select_one(f"#{section_id} table.data-table") or soup.select_one(f"#{section_id} table")
    if not tb:
        return None
    cols = [th.get_text(strip=True) for th in tb.select("thead th")][1:]
    rows, links = {}, {}
    for tr in tb.select("tbody tr"):
        tds = tr.select("td")
        if not tds:
            continue
        label = re.sub(r"\s*\+\s*$", "", tds[0].get_text(" ", strip=True)).strip()
        label = label.split("  ")[0]
        if label.startswith("CFO/EBITDA"):
            label = "CFO/EBITDA"
        if label == "Raw PDF":
            links = {cols[i]: (a.get("href") if (a := td.select_one("a")) else None) for i, td in enumerate(tds[1:])}
            continue
        rows[label] = [_num(td.get_text(strip=True)) for td in tds[1:]]
    return {"cols": cols, "rows": rows, "pdf": links}


def _fetch_page(symbol: str, consolidated: bool):
    url = f"{BASE}/company/{symbol}/{'consolidated/' if consolidated else ''}"
    return url, net.get(url).text


def _has_data(soup) -> bool:
    t = _table(soup, "quarters")
    if not t or not t["rows"]:
        return False
    first = next(iter(t["rows"].values()))
    return any(v not in (None, 0) for v in first)


def _parse_announcements(container):
    out = []
    for li in container.select("li"):
        a = li.select_one("a")
        t = li.select_one("time")
        if not a or not t:
            continue
        sub = a.select_one("div")
        summary = ""
        if sub:
            summary = sub.get_text(" ", strip=True)
            summary = re.sub(r"^\d{1,2} \w{3}( \d{4})?\s*-\s*", "", summary)
            sub.extract()
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        out.append({
            "title": title,
            "summary": summary.rstrip("… ").strip(),
            "date": t.get("datetime"),
            "url": a.get("href"),
        })
    return out


@store.cached("screener", ttl=SIX_HOURS)
def company(symbol: str):
    symbol = symbol.upper().strip()
    url, html = _fetch_page(symbol, True)
    soup = BeautifulSoup(html, "html.parser")
    consolidated = True
    if not _has_data(soup):
        url, html = _fetch_page(symbol, False)
        soup = BeautifulSoup(html, "html.parser")
        consolidated = False
    h1 = soup.select_one("h1")
    m = re.search(r'data-company-id="(\d+)"', html)
    bse = re.search(r"bseindia\.com/stock-share-price/[^\"]+?/(\d{6})/", html)
    ratios = {}
    for li in soup.select("#top-ratios li"):
        n, v = li.select_one(".name"), li.select_one(".number")
        if n and v:
            ratios[n.get_text(strip=True)] = _num(v.get_text(strip=True))
    about = soup.select_one(".company-profile .about p") or soup.select_one(".about p")

    docs = soup.select_one("#documents")
    anns = _parse_announcements(docs.select_one("#company-announcements-tab").parent) if docs else []
    annual = []
    concalls = []
    if docs:
        for blk in docs.select(".documents"):
            head = blk.select_one("h3")
            head = head.get_text(strip=True) if head else ""
            if head.startswith("Annual reports"):
                for a in blk.select("li a"):
                    txt = a.get_text(" ", strip=True)
                    yr = re.search(r"(\d{4})", txt)
                    if a.get("href", "").endswith(".pdf"):
                        annual.append({"year": int(yr.group(1)) if yr else None, "url": a["href"]})
            elif head.startswith("Concalls"):
                for li in blk.select("li"):
                    month = li.select_one("div")
                    item = {"month": month.get_text(strip=True) if month else ""}
                    for a in li.select("a.button-chip"):
                        kind = a.get_text(strip=True).lower()
                        if kind in ("transcript", "ppt", "rec"):
                            item[kind] = a.get("href")
                    concalls.append(item)
    return {
        "symbol": symbol,
        "name": h1.get_text(strip=True) if h1 else symbol,
        "url": url,
        "consolidated": consolidated,
        "company_id": m.group(1) if m else None,
        "bse_code": bse.group(1) if bse else None,
        "about": about.get_text(" ", strip=True) if about else "",
        "ratios": ratios,
        "quarters": _table(soup, "quarters"),
        "annual": _table(soup, "profit-loss"),
        "balance": _table(soup, "balance-sheet"),
        "cashflow": _table(soup, "cash-flow"),
        "ratio_table": _table(soup, "ratios"),
        "announcements": anns,
        "annual_reports": annual,
        "concalls": concalls,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


@store.cached("screener-ann", ttl=20 * 60)
def announcements(symbol: str):
    """Recent + 'important' filings. Screener exposes the latest ~5 of each to
    anonymous users, which covers a 24h-7d window for most companies."""
    c = company(symbol)
    items = list(c["announcements"])
    if c.get("company_id"):
        for kind in ("recent", "important"):
            try:
                html = net.get(f"{BASE}/announcements/{kind}/{c['company_id']}/").text
                items += _parse_announcements(BeautifulSoup(html, "html.parser"))
            except Exception:
                pass
    seen, out = set(), []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        out.append(it)
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    return out


@store.cached("screener-search", ttl=7 * 86400)
def search(q: str):
    r = net.get(f"{BASE}/api/company/search/?q={q}&v=3&fts=1")
    out = []
    for it in r.json():
        m = re.match(r"/company/([^/]+)/", it.get("url") or "")
        if it.get("id") and m:
            out.append({"symbol": m.group(1), "name": it["name"]})
    return out

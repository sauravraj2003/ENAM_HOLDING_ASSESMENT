"""End-of-day price & volume from Yahoo Finance (yfinance), batched in one call."""

import logging
import math
import warnings

import store

warnings.filterwarnings("ignore")
log = logging.getLogger(__name__)


def _yahoo(symbol: str) -> str:
    return symbol if symbol.endswith((".NS", ".BO")) else f"{symbol}.NS"


@store.cached("prices", ttl=30 * 60)
def history(symbols: tuple, period: str = "6mo"):
    """{symbol: [{d, o, h, l, c, v}, ...]} oldest first."""
    import yfinance as yf

    tickers = [_yahoo(s) for s in symbols]
    df = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    out = {}
    for sym, t in zip(symbols, tickers):
        try:
            sub = df[t] if len(tickers) > 1 else df.droplevel(1, axis=1) if df.columns.nlevels > 1 else df
            sub = sub.dropna(subset=["Close"])
        except Exception:
            continue
        bars = []
        for idx, row in sub.iterrows():
            v = row.get("Volume")
            bars.append({
                "d": idx.strftime("%Y-%m-%d"),
                "o": round(float(row["Open"]), 2), "h": round(float(row["High"]), 2),
                "l": round(float(row["Low"]), 2), "c": round(float(row["Close"]), 2),
                "v": int(v) if v is not None and not math.isnan(v) else 0,
            })
        if bars:
            out[sym] = bars
    return out


def snapshot(bars: list, lookback: int = 20) -> dict:
    """Latest EOD stats used by the alert engine and the holdings list."""
    if len(bars) < 2:
        return {}
    last, prev = bars[-1], bars[-2]
    window = [b["v"] for b in bars[-lookback - 1:-1] if b["v"]]
    avg_v = sum(window) / len(window) if window else 0
    closes = [b["c"] for b in bars]
    hi = max(closes[-126:])
    return {
        "date": last["d"],
        "close": last["c"],
        "prev_close": prev["c"],
        "change_pct": round((last["c"] / prev["c"] - 1) * 100, 2) if prev["c"] else 0,
        "volume": last["v"],
        "avg_volume": round(avg_v),
        "vol_multiple": round(last["v"] / avg_v, 2) if avg_v else None,
        "from_6m_high_pct": round((last["c"] / hi - 1) * 100, 1) if hi else None,
        "ret_1m_pct": round((last["c"] / closes[-22] - 1) * 100, 1) if len(closes) > 22 else None,
        "spark": closes[-60:],
        "vol_spark": [b["v"] for b in bars[-30:]],
    }

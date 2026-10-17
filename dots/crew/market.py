"""Alpaca market data for QUANT and RISK: quotes, daily bars, and a plain fee/slippage model."""
from __future__ import annotations

import math
from typing import Optional

import requests

from .config import settings


class AlpacaData:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
        )
        self.base = settings.alpaca_data_url.rstrip("/")

    def _get(self, path: str, **params) -> Optional[dict]:
        if not settings.alpaca_configured:
            return None
        try:
            r = self.session.get(f"{self.base}{path}", params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            return None

    def quote(self, symbol: str) -> Optional[dict]:
        data = self._get(f"/v2/stocks/{symbol}/quotes/latest", feed="iex")
        return (data or {}).get("quote")

    def last_price(self, symbol: str) -> Optional[float]:
        """Mid of a sane two-sided quote; otherwise the last daily close.

        Outside market hours the IEX quote is often one-sided or far off, so a wide or one-sided
        quote is not trusted.
        """
        q = self.quote(symbol)
        if q:
            ask, bid = float(q.get("ap") or 0), float(q.get("bp") or 0)
            if ask and bid and ask >= bid:
                mid = (ask + bid) / 2
                if (ask - bid) / mid <= 0.02:
                    return mid
        bars = self.daily_bars(symbol, limit=5)
        return float(bars[-1]["c"]) if bars else None

    def daily_bars(self, symbol: str, limit: int = 30) -> list[dict]:
        from datetime import date, timedelta

        start = (date.today() - timedelta(days=int(limit * 1.6) + 7)).isoformat()
        # The API returns bars oldest-first and stops at `limit`, so ask for plenty and keep the newest.
        data = self._get(f"/v2/stocks/{symbol}/bars", timeframe="1Day", start=start, limit=1000, feed="iex")
        return ((data or {}).get("bars") or [])[-limit:]


def bars_since(data: "AlpacaData", symbol: str, start_date: str, limit: int = 60) -> list[dict]:
    """Daily bars from start_date onward (IEX feed), oldest first."""
    got = data._get(f"/v2/stocks/{symbol}/bars", timeframe="1Day", start=start_date, limit=limit, feed="iex")
    return (got or {}).get("bars") or []


def gap_stats(bars: list[dict]) -> dict:
    """Overnight gap behaviour RISK uses to judge whether a stop is realistic."""
    gaps = []
    for prev, cur in zip(bars, bars[1:]):
        if prev.get("c"):
            gaps.append(abs(float(cur["o"]) - float(prev["c"])) / float(prev["c"]))
    if not gaps:
        return {"n": 0, "mean_gap_pct": None, "max_gap_pct": None}
    return {
        "n": len(gaps),
        "mean_gap_pct": round(100 * sum(gaps) / len(gaps), 2),
        "max_gap_pct": round(100 * max(gaps), 2),
    }


def cost_model(quote: Optional[dict], notional: float) -> dict:
    """Dollar costs QUANT must beat. Commission is zero on Alpaca paper; spread and slippage are not."""
    if not quote or not notional:
        return {"commission_usd": 0.0, "spread_usd": None, "slippage_usd": None}
    ask, bid = float(quote.get("ap") or 0), float(quote.get("bp") or 0)
    mid = (ask + bid) / 2 if ask and bid else 0
    spread_pct = (ask - bid) / mid if mid else 0.0
    return {
        "commission_usd": 0.0,
        "spread_usd": round(notional * spread_pct / 2, 4),  # you cross half the spread each way
        "slippage_usd": round(notional * 0.0005, 4),  # 5 bps assumption for a small order
        "spread_pct": round(100 * spread_pct, 3),
    }


def shares_for(notional: float, price: float) -> int:
    return int(math.floor(notional / price)) if price > 0 else 0

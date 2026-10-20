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


def extended_move(data: "AlpacaData", symbol: str, now=None) -> Optional[dict]:
    """The stock's move outside regular hours, for whichever extended session the run sits in.

    Before 9:30 New York time: pre-market from 4:00 against the last regular close.
    After 4:00pm: after-hours up to 8:00pm against today's regular close.
    Uses the free IEX feed, which is thin outside regular hours; treat small volumes with care.
    """
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    now = (now or datetime.now(timezone.utc)).astimezone(ny)
    if now.weekday() >= 5:
        return None
    day = now.date()
    at = lambda h, m=0: datetime(day.year, day.month, day.day, h, m, tzinfo=ny)
    daily = data.daily_bars(symbol, 10)
    if now < at(9, 30):
        session, start, end = "pre-market", at(4), now
        prior = [b for b in daily if b["t"][:10] < day.isoformat()]
    elif now >= at(16):
        session, start, end = "after-hours", at(16), min(now, at(20))
        prior = [b for b in daily if b["t"][:10] <= day.isoformat()]
    else:
        return None
    if not prior:
        return None
    ref = float(prior[-1]["c"])
    got = data._get(f"/v2/stocks/{symbol}/bars", timeframe="5Min", start=start.isoformat(), end=end.isoformat(),
                    limit=1000, feed="iex")
    bars = (got or {}).get("bars") or []
    if not bars:
        return {"session": session, "reference_close": ref, "trades_seen": False,
                "note": "no extended-hours trades on the IEX feed yet"}
    last = float(bars[-1]["c"])
    return {
        "session": session,
        "reference_close": ref,
        "last": last,
        "move_pct": round(100 * (last / ref - 1), 2),
        "high": max(float(b["h"]) for b in bars),
        "low": min(float(b["l"]) for b in bars),
        "volume": int(sum(float(b["v"]) for b in bars)),
        "as_of": bars[-1]["t"],
        "trades_seen": True,
        "note": "IEX feed only; extended-hours volume is thin, so a small print can move the last price",
    }


def in_regular_session(now=None) -> bool:
    """True between 9:30 and 4:00pm New York time on a weekday (holidays aside)."""
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo

    t = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    return t.weekday() < 5 and time(9, 30) <= t.time() < time(16)


def move_since(data: "AlpacaData", symbol: str, since, now=None) -> Optional[dict]:
    """How far the stock has moved since `since` (a news or filing time), while the market is open.

    News from before today's open is measured from the previous regular close, so the pre-market
    reaction counts. News during the session is measured from the last 5-minute bar before it.
    None outside regular hours, or for news older than the previous close.
    """
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    now = now or datetime.now(timezone.utc)
    if not in_regular_session(now) or since >= now:
        return None
    day = now.astimezone(ny).date()
    open_ = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ny)
    if since < open_:
        prior = [b for b in data.daily_bars(symbol, 10) if b["t"][:10] < day.isoformat()]
        if not prior:
            return None
        ref, start, basis = float(prior[-1]["c"]), open_, "previous close"
    else:
        start, ref, basis = since, None, "last bar before the news"
    got = data._get(f"/v2/stocks/{symbol}/bars", timeframe="5Min", start=(start - timedelta(minutes=30)).isoformat(),
                    end=now.isoformat(), limit=1000, feed="iex")
    bars = (got or {}).get("bars") or []
    stamp = lambda b: datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
    after = [b for b in bars if stamp(b) + timedelta(minutes=5) > start]
    if not after:
        return None
    if ref is None:
        before = [b for b in bars if stamp(b) + timedelta(minutes=5) <= start]
        ref = float(before[-1]["c"]) if before else float(after[0]["o"])
    last = float(after[-1]["c"])
    return {
        "since": since.isoformat(),
        "reference": ref,
        "reference_is": basis,
        "last": last,
        "move_pct": round(100 * (last / ref - 1), 2),
        "high": max(float(b["h"]) for b in after),
        "low": min(float(b["l"]) for b in after),
        "volume": int(sum(float(b["v"]) for b in after)),
        "as_of": after[-1]["t"],
        "note": "IEX feed only: the move since the news went out, during today's session",
    }


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

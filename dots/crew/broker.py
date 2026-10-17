"""Paper execution. Refuses anything that is not Alpaca's paper endpoint, and never sends an
order unless the Spider explicitly asks with a complete bracket (entry, stop, target)."""
from __future__ import annotations

from typing import Optional

import requests

from .config import settings


class PaperOnlyError(RuntimeError):
    pass


class PaperBroker:
    def __init__(self, dry_run: bool = False):
        if not settings.paper_only:
            raise PaperOnlyError(
                f"ALPACA_PAPER_URL is {settings.alpaca_paper_url!r}; this crew only trades paper."
            )
        self.dry_run = dry_run or not settings.alpaca_configured
        self.session = requests.Session()
        self.session.headers.update(
            {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
        )
        self.base = settings.alpaca_paper_url.rstrip("/")

    @property
    def mode(self) -> str:
        return "dry-run" if self.dry_run else "alpaca-paper"

    def equity(self) -> float:
        if self.dry_run:
            return settings.start_balance
        r = self.session.get(f"{self.base}/v2/account", timeout=20)
        r.raise_for_status()
        return float(r.json()["equity"])

    def open_positions(self) -> list[dict]:
        if self.dry_run:
            return []
        r = self.session.get(f"{self.base}/v2/positions", timeout=20)
        r.raise_for_status()
        return r.json()

    def bracket_order(
        self, symbol: str, side: str, qty: int, entry: float, stop: float, target: float
    ) -> dict:
        if qty < 1:
            raise ValueError("bracket orders need at least one whole share")
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "limit",
            "limit_price": f"{entry:.2f}",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{target:.2f}"},
            "stop_loss": {"stop_price": f"{stop:.2f}"},
        }
        if self.dry_run:
            return {"id": "dry-run", "status": "not_sent", "mode": self.mode, **body}
        r = self.session.post(f"{self.base}/v2/orders", json=body, timeout=20)
        r.raise_for_status()
        data = r.json()
        return {"id": data.get("id"), "status": data.get("status"), "mode": self.mode, **body}

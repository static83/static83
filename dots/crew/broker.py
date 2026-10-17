"""Paper execution. Refuses anything that is not Alpaca's paper endpoint, and never sends an
order unless the Spider explicitly asks with a complete bracket (entry, stop, target)."""
from __future__ import annotations

from typing import Optional

import json

import requests

from .config import RUNS_DIR, settings

BASELINE_FILE = RUNS_DIR / "balance_baseline.json"


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
        self._baseline: Optional[float] = None

    @property
    def mode(self) -> str:
        return "dry-run" if self.dry_run else "alpaca-paper"

    def raw_equity(self) -> float:
        r = self.session.get(f"{self.base}/v2/account", timeout=20)
        r.raise_for_status()
        return float(r.json()["equity"])

    def equity(self) -> float:
        """Account equity the crew sizes against.

        With DOTS_BALANCE_CAP set, the crew acts as if it has exactly that much: the cap plus
        any profit or loss since this run started. The real paper balance is untouched.
        """
        if self.dry_run:
            return settings.balance_cap or settings.start_balance
        real = self.raw_equity()
        if not settings.balance_cap:
            return real
        if self._baseline is None:
            # The baseline is fixed the first night and kept, so profit and loss carry over.
            if BASELINE_FILE.exists():
                self._baseline = float(json.loads(BASELINE_FILE.read_text())["real_equity"])
            else:
                self._baseline = real
                BASELINE_FILE.parent.mkdir(exist_ok=True)
                BASELINE_FILE.write_text(json.dumps({"real_equity": real, "cap": settings.balance_cap}))
        return round(settings.balance_cap + (real - self._baseline), 2)

    def open_positions(self) -> list[dict]:
        if self.dry_run:
            return []
        r = self.session.get(f"{self.base}/v2/positions", timeout=20)
        r.raise_for_status()
        return r.json()

    def open_orders(self) -> list[dict]:
        if self.dry_run:
            return []
        r = self.session.get(f"{self.base}/v2/orders", params={"status": "open"}, timeout=20)
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

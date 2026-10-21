"""Paper execution. Refuses anything that is not Alpaca's paper endpoint, and never sends an
order unless the Spider explicitly asks with a complete bracket (entry, stop, target)."""
from __future__ import annotations

from typing import Optional

import json

import requests

from .config import RUNS_DIR, settings

BASELINE_FILE = RUNS_DIR / "balance_baseline.json"
# Share of the planned room between entry and stop that must still be there for the entry to fill.
# A buy triggers only at or above entry - 0.5 x (entry - stop); a short only at or below the mirror level.
ENTRY_GUARD = 0.5


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

    def shortable(self, symbol: str) -> bool:
        if self.dry_run:
            return True
        r = self.session.get(f"{self.base}/v2/assets/{symbol}", timeout=20)
        if r.status_code != 200:
            return False
        a = r.json()
        return bool(a.get("shortable") and a.get("easy_to_borrow"))

    def cancel_stale_entries(self, max_age_hours: float = 20) -> list[str]:
        """Cancel bracket entries that never filled. Cancelling the parent cancels its legs."""
        if self.dry_run:
            return []
        from datetime import datetime, timezone

        r = self.session.get(f"{self.base}/v2/orders", params={"status": "open", "nested": "true"}, timeout=20)
        r.raise_for_status()
        now = datetime.now(timezone.utc)
        cancelled = []
        for o in r.json():
            if o.get("order_class") != "bracket" or float(o.get("filled_qty") or 0) > 0:
                continue
            if not o.get("legs"):
                # After an entry fills, Alpaca lists its stop and target as orders of their own,
                # still marked bracket and unfilled. They protect a live position: never cancel them.
                continue
            created = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
            if (now - created).total_seconds() / 3600 >= max_age_hours:
                d = self.session.delete(f"{self.base}/v2/orders/{o['id']}", timeout=20)
                if d.status_code in (200, 204):
                    cancelled.append(f"{o['symbol']} {o['side']} {o['qty']} @ {o.get('limit_price')}")
        return cancelled

    def get_order(self, order_id: str) -> Optional[dict]:
        if self.dry_run or not order_id or order_id == "dry-run":
            return None
        r = self.session.get(f"{self.base}/v2/orders/{order_id}", params={"nested": "true"}, timeout=20)
        return r.json() if r.status_code == 200 else None

    def replace_order(self, order_id: str, **prices: float) -> Optional[dict]:
        """Reprice a working order (a bracket's stop or target leg). Alpaca answers with the replacement."""
        if self.dry_run:
            return None
        body = {k: f"{v:.2f}" for k, v in prices.items()}
        r = self.session.patch(f"{self.base}/v2/orders/{order_id}", json=body, timeout=20)
        return r.json() if r.status_code == 200 else None

    def cancel_order(self, order_id: str) -> bool:
        if self.dry_run:
            return False
        r = self.session.delete(f"{self.base}/v2/orders/{order_id}", timeout=20)
        return r.status_code in (200, 204)

    def close_position(self, symbol: str) -> Optional[dict]:
        """Market order to flatten one symbol. Queued for the next open if the market is closed."""
        if self.dry_run:
            return None
        r = self.session.delete(f"{self.base}/v2/positions/{symbol}", timeout=20)
        return r.json() if r.status_code in (200, 207) else None

    def trading_days_between(self, start_date: str, end_date: str) -> int:
        """Sessions strictly after start_date up to and including end_date (YYYY-MM-DD)."""
        if self.dry_run:
            return 0
        r = self.session.get(f"{self.base}/v2/calendar", params={"start": start_date, "end": end_date}, timeout=20)
        r.raise_for_status()
        return sum(1 for d in r.json() if start_date < d["date"] <= end_date)

    def bracket_order(
        self, symbol: str, side: str, qty: int, entry: float, stop: float, target: float
    ) -> dict:
        if qty < 1:
            raise ValueError("bracket orders need at least one whole share")
        # The entry is a stop-limit: it only goes live while the price is on the entry's side of a
        # trigger halfway between the stop and the entry. An open that gaps through the stop never
        # triggers it, so the crew is not filled on top of its own stop (see ENTRY_GUARD).
        trigger = entry - ENTRY_GUARD * (entry - stop)
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "stop_limit",
            "stop_price": f"{trigger:.2f}",
            "limit_price": f"{entry:.2f}",
            # gtc so the stop-loss and take-profit legs stay live after the first session.
            # An entry that has not filled by the next night is cancelled by cancel_stale_entries().
            "time_in_force": "gtc",
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

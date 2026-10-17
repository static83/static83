"""The night's record. Every handoff becomes an Event; the dashboard replays the JSON this writes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import ROLE_ORDER, RUNS_DIR, ROOT, settings
from .schema import CaseResult, Event, Memo

DASHBOARD_LATEST = ROOT / "dashboard" / "runs" / "latest.json"


class Ledger:
    def __init__(self, start_balance: float, mode: str, model: str, clock=None):
        self.started = datetime.now(timezone.utc)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.start_balance = start_balance
        self.mode = mode
        self.model = model
        self.events: list[Event] = []
        self.balance: list[tuple[int, float]] = [(0, start_balance)]
        self.cases: list[CaseResult] = []
        self.counts = {"objections": 0, "source_links": 0, "send_backs": 0, "restarts": 0}
        self.track_record: dict = {}

    # ---- writing ----------------------------------------------------------------------------
    def minute(self) -> int:
        return int((self.clock() - self.started).total_seconds() // 60)

    def log(self, role: str, kind: str, case: int, text: str) -> Event:
        ev = Event(t=self.clock().isoformat(), minute=self.minute(), role=role, kind=kind, case=case, text=text)
        self.events.append(ev)
        stamp = self.clock().strftime("%H:%M")
        print(f"  {stamp}  {role:<8} {text}")
        return ev

    def memo(self, case: int, memo: Memo) -> None:
        self.log(memo.role, "handoff", case, memo.headline)
        for obj in memo.objections:
            self.counts["objections"] += 1
            self.log(memo.role, "objection", case, obj)

    def mark_balance(self, usd: float) -> None:
        self.balance.append((self.minute(), round(usd, 2)))

    # ---- output -----------------------------------------------------------------------------
    def receipts(self) -> dict[str, int]:
        out = {r: 0 for r in ROLE_ORDER}
        for ev in self.events:
            if ev.role in out:
                out[ev.role] += 1
        return out

    def to_dashboard(self) -> dict:
        cases = []
        for c in self.cases:
            quant = next((m for m in c.memos if m.role == "QUANT"), None)
            risk = next((m for m in c.memos if m.role == "RISK"), None)
            cases.append(
                {
                    "id": c.case,
                    "ticker": c.ticker,
                    "outcome": c.outcome,
                    "rounds": c.rounds,
                    "entry": quant.entry_price if quant else None,
                    "stop": quant.stop_price if quant else None,
                    "target": quant.target_price if quant else None,
                    "size": (risk.position_size_usd if risk and risk.position_size_usd else None)
                    or (quant.position_size_usd if quant else None),
                    "p_strike": quant.p_strike if quant else None,
                    "risk": {"agree": "APPROVED", "object": "REJECTED", "send_back": "SENT BACK"}.get(
                        risk.verdict if risk else "", "PENDING"
                    ),
                    "objections": sum(len(m.objections) for m in c.memos),
                    "order": c.order,
                }
            )
        hours = max(1, -(-max(1, self.minute()) // 60))
        return {
            "title": "DOTS night run",
            "fictional": False,
            "mode": self.mode,
            "model": self.model,
            "router": "SPIDER",
            "started_at": self.started.isoformat(),
            "start_balance": self.start_balance,
            "hours": hours,
            "balance": [list(b) for b in self.balance],
            "events": [{"m": e.minute, "role": e.role, "kind": e.kind, "case": e.case, "text": e.text} for e in self.events],
            "cases": cases,
            "receipts": self.receipts(),
            "counts": self.counts,
            "track_record": self.track_record,
        }

    def write(self, path: Optional[Path] = None) -> Path:
        RUNS_DIR.mkdir(exist_ok=True)
        DASHBOARD_LATEST.parent.mkdir(parents=True, exist_ok=True)
        path = path or RUNS_DIR / f"{self.started.strftime('%Y%m%d-%H%M')}.json"
        data = self.to_dashboard()
        path.write_text(json.dumps(data, indent=2))
        DASHBOARD_LATEST.write_text(json.dumps(data, indent=2))
        (RUNS_DIR / f"{path.stem}-memos.json").write_text(
            json.dumps([c.model_dump() for c in self.cases], indent=2, default=str)
        )
        return path

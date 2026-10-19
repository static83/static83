"""The Spider: routes one case through the six Dots and enforces crew/roles/spider.md.

It is deliberately plain code. Every rule in spider.md maps to a few lines here, in order.
"""
from __future__ import annotations

from typing import Callable, Optional

from . import learning
from .config import ROLE_ORDER, settings
from .ledger import Ledger
from .market import AlpacaData, cost_model, extended_move, gap_stats, shares_for
from .schema import Candidate, CaseResult, Memo

Asker = Callable[[str, dict], Memo]
PIPELINE = ["ANALYST", "SKEPTIC", "QUANT", "RISK"]
MAX_BAD_EXIT_FRACTION = 0.05  # RISK rule of thumb surfaced to the model; RISK may be stricter


class Spider:
    def __init__(self, ask: Asker, ledger: Ledger, broker, data: Optional[AlpacaData] = None,
                 track_record: Optional[dict] = None, record: bool = False):
        self.ask = ask
        self.ledger = ledger
        self.broker = broker
        self.data = data or AlpacaData()
        self.track_record = track_record or {}
        self.record = record  # write cases and trades to the casebook (off for offline demos)
        self.night = ledger.started.strftime("%Y%m%d-%H%M")

    def case_key(self, case: int, cand: Candidate) -> str:
        return f"{self.night}-{case:02d}-{cand.ticker}"

    # ---- rule 1: one case, fixed order, everyone sees everything --------------------------
    def packet(self, case: int, cand: Candidate, memos: list[Memo], extra: dict) -> dict:
        return {
            "case": case,
            "account": {
                "equity_usd": self.ledger.start_balance,
                "mode": self.broker.mode,
                "open_positions": [p.get("symbol") for p in self.broker.open_positions()],
                "open_orders": [o.get("symbol") for o in self.broker.open_orders()],
            },
            "filing": cand.model_dump(),
            "market": extra,
            "memos_so_far": [m.model_dump(exclude_none=True) for m in memos],
            "track_record": self.track_record,
            "rules": {
                "unanimous_first_pass_restarts": True,
                "max_rounds": settings.max_rounds,
                "max_bad_exit_fraction_of_account": MAX_BAD_EXIT_FRACTION,
                "time_limit": f"trades close after horizon_days trading days (default {learning.DEFAULT_HORIZON_DAYS})",
            },
        }

    def market_context(self, cand: Candidate) -> dict:
        quote = self.data.quote(cand.ticker)
        bars = self.data.daily_bars(cand.ticker)
        return {
            "quote": quote,
            "extended_hours": extended_move(self.data, cand.ticker),
            "gap_stats_30d": gap_stats(bars),
            "cost_model_for_full_account": cost_model(quote, self.ledger.start_balance),
            "note": "quote is None when Alpaca keys are not configured; say so instead of guessing",
        }

    def run_case(self, case: int, cand: Candidate) -> CaseResult:
        L = self.ledger
        L.counts["source_links"] += 1
        source = "news" if cand.items == ["news"] else f"8-K items {', '.join(cand.items)}"
        L.log("SPIDER", "system", case, f"case {case:02d} opened: {cand.ticker} {source}")
        market = self.market_context(cand)
        memos: list[Memo] = []

        scout = self.ask("SCOUT", self.packet(case, cand, memos, market))
        memos.append(scout)
        L.memo(case, scout)
        if scout.verdict != "agree":
            return self.close(case, cand, memos, "no_trade", 1)

        rounds = 1
        stage = 0
        while True:
            role = PIPELINE[stage]
            memo = self.ask(role, self.packet(case, cand, memos, market))
            memos.append(memo)
            L.memo(case, memo)

            # rule 4: send-back is a real move, bounded by max_rounds
            if memo.verdict == "send_back":
                target = memo.send_back_to or "ANALYST"
                rounds += 1
                L.counts["send_backs"] += 1
                L.log("SPIDER", "send_back", case, f"{role} sent the case back to {target} (round {rounds})")
                if rounds > settings.max_rounds:
                    L.log("SPIDER", "system", case, "round cap hit, no trade")
                    return self.close(case, cand, memos, "no_trade", rounds)
                stage = 0 if target == "SCOUT" else max(0, PIPELINE.index(target)) if target in PIPELINE else 0
                if target == "SCOUT":
                    scout = self.ask("SCOUT", self.packet(case, cand, memos, market))
                    memos.append(scout)
                    L.memo(case, scout)
                continue

            # rule 5: RISK's object ends the case; any other object is a no-trade too
            if memo.verdict == "object":
                L.log("SPIDER", "system", case, f"{role} objected: no trade")
                return self.close(case, cand, memos, "no_trade", rounds)

            stage += 1
            if stage < len(PIPELINE):
                continue

            # rule 2: all agree on the first pass with no objections -> start over
            total_objections = sum(len(m.objections) for m in memos)
            if rounds == 1 and total_objections == 0:
                L.counts["restarts"] += 1
                L.log("SPIDER", "restart", case, "six agreed right away. starting over.")
                return self.close(case, cand, memos, "restarted", rounds)

            # rule 6: a trade needs numbers
            return self.execute(case, cand, memos, rounds, market)

    # ---- rule 6 and 7: numbers, then paper ------------------------------------------------
    def execute(self, case: int, cand: Candidate, memos: list[Memo], rounds: int, market: dict) -> CaseResult:
        L = self.ledger
        quant = next((m for m in reversed(memos) if m.role == "QUANT"), None)
        risk = next((m for m in reversed(memos) if m.role == "RISK"), None)
        needed = [quant and quant.entry_price, quant and quant.stop_price, quant and quant.target_price,
                  quant and quant.bad_exit_cost_usd is not None, risk and risk.position_size_usd]
        if not all(needed):
            L.log("SPIDER", "system", case, "RISK approved but numbers are incomplete: no trade")
            return self.close(case, cand, memos, "rejected", rounds)
        held = [p.get("symbol") for p in self.broker.open_positions()] + [o.get("symbol") for o in self.broker.open_orders()]
        if held:
            L.log("SPIDER", "system", case, f"already holding or waiting on {', '.join(sorted(set(held)))}: one position at a time, no trade")
            return self.close(case, cand, memos, "rejected", rounds)
        size = min(risk.position_size_usd, quant.position_size_usd or risk.position_size_usd)
        qty = shares_for(size, quant.entry_price)
        side = "sell" if (quant.direction or risk.direction) == "short" else "buy"
        if side == "sell" and not self.broker.shortable(cand.ticker):
            L.log("SPIDER", "system", case, f"{cand.ticker} is not shortable or not easy to borrow at Alpaca: no trade")
            return self.close(case, cand, memos, "rejected", rounds)
        if qty < 1:
            L.log("SPIDER", "system", case, f"approved size ${size:.2f} buys 0 shares at {quant.entry_price:.2f}: no trade")
            return self.close(case, cand, memos, "rejected", rounds)
        order = self.broker.bracket_order(cand.ticker, side, qty, quant.entry_price, quant.stop_price, quant.target_price)
        horizon = learning.horizon_of(memos)
        L.log("SPIDER", "order", case,
              f"{order['mode']}: {side} {qty} {cand.ticker} @ {quant.entry_price:.2f} stop {quant.stop_price:.2f} "
              f"target {quant.target_price:.2f}, time limit {horizon} trading days")
        if self.record:
            learning.record_trade(self.case_key(case, cand), order, memos)
        return self.close(case, cand, memos, "paper_order", rounds, order)

    # ---- JOURNAL closes every case ----------------------------------------------------------
    def close(self, case, cand, memos, outcome, rounds, order=None) -> CaseResult:
        packet = self.packet(case, cand, memos, {"outcome": outcome, "order": order})
        journal = self.ask("JOURNAL", packet)
        memos.append(journal)
        self.ledger.log("JOURNAL", "report", case, journal.headline)
        result = CaseResult(case=case, ticker=cand.ticker, outcome=outcome, rounds=rounds, memos=memos, order=order)
        self.ledger.cases.append(result)
        if self.record:
            learning.record_case(self.night, result, cand, self.ledger.clock())
        return result

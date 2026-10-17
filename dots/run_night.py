#!/usr/bin/env python3
"""Run one night of the DOTS crew.

    python run_night.py                 # live EDGAR + Claude, Alpaca paper if keys exist, else dry-run
    python run_night.py --dry-run       # never send orders, even with Alpaca keys
    python run_night.py --offline       # no network, no keys: scripted memos, exercises the pipeline
    python run_night.py --candidates 2  # cap SCOUT's handoffs for the night
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from crew.broker import PaperBroker
from crew.config import settings
from crew.ledger import Ledger
from crew.market import AlpacaData
from crew.spider import Spider


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="scripted memos, no API calls")
    ap.add_argument("--dry-run", action="store_true", help="never send paper orders")
    ap.add_argument("--candidates", type=int, default=settings.max_candidates)
    args = ap.parse_args()

    broker = PaperBroker(dry_run=args.dry_run or args.offline)
    if args.offline:
        from crew.offline import SAMPLE_FILINGS, FakeClock, OfflineAsker

        clock = FakeClock(datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc))
        ledger = Ledger(settings.start_balance, broker.mode, "offline-script", clock=clock)
        ledger.started = clock.now
        ask = OfflineAsker(clock)
        candidates = SAMPLE_FILINGS[: args.candidates]
        data = AlpacaData()
    else:
        if not settings.anthropic_api_key:
            print("ANTHROPIC_API_KEY is not set (or run `ant auth login`). Use --offline to test without it.")
            return 2
        from crew.llm import ask_role as ask
        from crew.scout import Edgar, find_candidates

        data = AlpacaData()
        ledger = Ledger(broker.equity(), broker.mode, settings.model)
        print(f"22:00 rule: if all six agree right away, start over. broker={broker.mode} model={settings.model}")
        print("SCOUT scanning EDGAR 8-K feed...")
        candidates = find_candidates(Edgar(), data.last_price, limit=args.candidates)
        if not candidates:
            print("SCOUT found nothing worth the crew's night. No trade.")
            ledger.log("SCOUT", "system", 0, "no candidates passed the filters")
            ledger.write()
            return 0

    spider = Spider(ask, ledger, broker, data)
    for i, cand in enumerate(candidates, start=1):
        result = spider.run_case(i, cand)
        if result.outcome == "paper_order":
            ledger.mark_balance(broker.equity())
            break  # one name, one night
    ledger.mark_balance(broker.equity())
    path = ledger.write()
    outcomes = ", ".join(f"case {c.case:02d} {c.ticker} {c.outcome}" for c in ledger.cases)
    print(f"\nnight closed: {outcomes}\nledger: {path}\ndashboard: dashboard/runs/latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

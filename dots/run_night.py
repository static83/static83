#!/usr/bin/env python3
"""Run one night of the DOTS crew.

    python run_night.py                 # live EDGAR + Claude, Alpaca paper if keys exist, else dry-run
    python run_night.py --dry-run       # never send orders, even with Alpaca keys
    python run_night.py --offline       # no network, no keys: scripted memos, exercises the pipeline
    python run_night.py --candidates 2  # cap SCOUT's handoffs for the night
    python run_night.py --manual runs/night1   # memos written by hand into runs/night1/inbox
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from crew.broker import PaperBroker
from crew.config import settings
from crew.ledger import Ledger
from crew.market import AlpacaData
from crew.spider import Spider


def ledger_path_for(manual_dir: str):
    from pathlib import Path

    from crew.config import RUNS_DIR

    return RUNS_DIR / f"{Path(manual_dir).name}.json"


def gather(edgar, data, args):
    """SEC filings and material news, heaviest events first (newest breaks ties), one case per company."""
    from crew.learning import recently_judged_tickers, seen_filings
    from crew.market import move_since
    from crew.news import fetch_news
    from crew.scout import find_candidates, find_news_candidates, material_news

    skip, recent = seen_filings(), recently_judged_tickers()
    now = datetime.now(timezone.utc)
    # one pull of the news feed serves both sources: news cases, and spotting filings already told.
    # It reaches at least 36 hours back so a filing or a rewrite can be matched to an earlier report.
    groups = {} if args.no_news else material_news(
        edgar, fetch_news(data, now - timedelta(hours=max(args.since_hours + 12, 36)), now))
    filings = find_candidates(edgar, data.last_price, data.daily_bars, limit=args.candidates * 5,
                              since_hours=args.since_hours, skip=skip, recent_tickers=recent, news=groups,
                              move_of=lambda t, since: move_since(data, t, since, now))
    news = [] if args.no_news else find_news_candidates(
        edgar, data, args.since_hours, skip=skip, recent_tickers=recent, now=now, groups=groups)

    def when(c):  # EDGAR stamps are New York time, Benzinga's are UTC: compare instants, not strings
        return datetime.fromisoformat(c.filed_at.replace("Z", "+00:00"))

    picked, tickers = [], set()
    for c in sorted(filings + news, key=lambda c: (c.weight, when(c)), reverse=True):
        if c.ticker in tickers:
            continue  # a filing and a news item about the same company: one case
        tickers.add(c.ticker)
        picked.append(c)
    return picked[: args.candidates]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="scripted memos, no API calls")
    ap.add_argument("--dry-run", action="store_true", help="never send paper orders")
    ap.add_argument("--candidates", type=int, default=settings.max_candidates)
    ap.add_argument("--since-hours", type=float, default=18, help="how far back SCOUT looks")
    ap.add_argument("--no-news", action="store_true", help="SEC filings only, skip the Benzinga news feed")
    ap.add_argument("--manual", metavar="DIR", help="no Anthropic key: memos are read from DIR/inbox")
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
    elif args.manual:
        from pathlib import Path

        from crew.manual import ManualAsker, OnceBroker, load_candidates, save_candidates
        from crew.scout import Edgar, find_candidates

        root = Path(args.manual)
        broker = OnceBroker(broker, root)
        data = AlpacaData()
        ask = ManualAsker(root)
        candidates = load_candidates(root)
        if candidates is None:
            print("SCOUT scanning EDGAR 8-K feed...")
            from crew.learning import seen_filings

            candidates = gather(Edgar(), data, args)
            save_candidates(root, candidates)
        if not candidates:
            print("SCOUT found nothing worth the crew's night. No trade.")
            return 0
        from datetime import datetime as _dt, timezone as _tz

        ask.now = _dt.fromtimestamp((root / "candidates.json").stat().st_mtime, _tz.utc)
        ledger = Ledger(broker.equity(), broker.mode, "claude (in session)", clock=ask.clock)
        ledger.started = ask.now
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
        from crew.learning import seen_filings

        candidates = gather(Edgar(), data, args)
        if not candidates:
            print("SCOUT found nothing worth the crew's night. No trade.")
            ledger.log("SCOUT", "system", 0, "no candidates passed the filters")
            ledger.write()
            return 0

    for stale in broker.cancel_stale_entries():
        ledger.log("SPIDER", "system", 0, f"cancelled unfilled entry from a previous night: {stale}")

    # Review before arguing: settle open trades (rule 8, the time limit), score past calls.
    track_record = None
    if not args.offline:
        from crew import learning

        learning.review_trades(broker, lambda text: ledger.log("SPIDER", "system", 0, text))
        learning.update_outcomes(data)
        track_record = learning.scorecard()
        learning.write_lessons(track_record)
        ledger.track_record = track_record
        ledger.log("JOURNAL", "report", 0, f"track record: {track_record['cases_on_file']} past case(s) on file")
        for line in track_record["scorecard"]:
            print(f"  record   {line}")
    spider = Spider(ask, ledger, broker, data, track_record=track_record, record=not args.offline)
    for i, cand in enumerate(candidates, start=1):
        try:
            result = spider.run_case(i, cand)
        except Exception as exc:  # manual mode pauses here until the memo is written
            if type(exc).__name__ != "NeedMemo":
                raise
            print(f"\nWAITING for memo {exc.key}\n  packet: {exc.path}")
            return 3
        if result.outcome == "paper_order":
            ledger.mark_balance(broker.equity())
            break  # one name, one night
    ledger.mark_balance(broker.equity())
    if not args.offline:  # the record the dashboard shows includes tonight's cases
        from crew import learning

        ledger.track_record = learning.scorecard()
        learning.write_lessons(ledger.track_record)
    # a manual run is named after its folder: the watcher and a scheduled run can start in the same minute
    path = ledger.write(ledger_path_for(args.manual) if args.manual else None)
    outcomes = ", ".join(f"case {c.case:02d} {c.ticker} {c.outcome}" for c in ledger.cases)
    print(f"\nnight closed: {outcomes}\nledger: {path}\ndashboard: dashboard/runs/latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

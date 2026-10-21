#!/usr/bin/env python3
"""The market watcher: looks at the news and EDGAR once a minute during regular hours.

It does not trade and does not judge. It exits, so whoever started it can act, when:

    ALERT    a heavy event (weight 3) passed every SCOUT filter and has not moved 3% yet: the cases
             are in runs/watch-<stamp>/candidates.json for `run_night.py --manual runs/watch-<stamp>`;
             or there is material news or a filing on a stock the crew holds or is waiting to buy:
             it is in runs/watch-<stamp>/held.json for a hold-or-exit call (position_review.py).
    TIMEOUT  it has run --max-minutes (default 110); start it again if the market is open.
    CLOSED   the regular session is over (or has not started).

    python watch.py                 # watch until an alert, the time limit, or the close
    python watch.py --status        # is a watcher alive?
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from crew import learning
from crew.broker import PaperBroker
from crew.config import RUNS_DIR
from crew.manual import save_candidates
from crew.market import AlpacaData, in_regular_session, move_since
from crew.news import fetch_news
from crew.scout import Edgar, find_candidates, find_news_candidates, material_news

NY = ZoneInfo("America/New_York")
WATCH = RUNS_DIR / "watch"
HEARTBEAT = WATCH / "heartbeat.json"
SEEN = WATCH / "seen.json"
LOG = WATCH / "watch.log"
POLL_SECONDS = 60
OVERLAP = timedelta(minutes=2)  # feeds can post an item a little after its timestamp
HISTORY = timedelta(hours=36)   # older headlines, kept to spot rewrites of stories already traded


def log(text: str) -> None:
    WATCH.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {text}\n")


def alive() -> bool:
    try:
        beat = json.loads(HEARTBEAT.read_text())
        os.kill(beat["pid"], 0)
    except (OSError, ValueError, KeyError):
        return False
    age = datetime.now(timezone.utc) - datetime.fromisoformat(beat["at"])
    return beat.get("state") == "watching" and age < timedelta(minutes=5)


def beat(state: str, last_poll: datetime, started: datetime) -> None:
    WATCH.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(json.dumps({"pid": os.getpid(), "state": state, "at": datetime.now(timezone.utc).isoformat(),
                                     "last_poll": last_poll.isoformat(), "started": started.isoformat()}))


def held_symbols(broker) -> set[str]:
    """Stocks the crew holds or has a working entry on; plumbing tests excluded."""
    held = {p.get("symbol") for p in broker.open_positions()} | {o.get("symbol") for o in broker.open_orders()}
    return held - learning.test_symbols()


def start_from(now: datetime) -> datetime:
    """Pick up where the last watcher stopped earlier today, else look back 15 minutes."""
    try:
        last = datetime.fromisoformat(json.loads(HEARTBEAT.read_text())["last_poll"])
        if now - last < timedelta(hours=3):
            return last
    except (OSError, ValueError, KeyError):
        pass
    return now - timedelta(minutes=15)


def poll(edgar, data, broker, tickers, raw: dict, seen: set[str], since: datetime, now: datetime):
    """One look at the feeds: (heavy new cases, alerts on held stocks)."""
    for item in fetch_news(data, since, now):
        raw[item["id"]] = item
    groups = material_news(edgar, sorted(raw.values(), key=lambda i: i["created_at"], reverse=True))
    held = held_symbols(broker)
    # only news after the crew opened the trade: what it traded on is not news to it
    opened = {t["symbol"]: datetime.fromisoformat(t["opened_at"]) for t in learning._load(learning.TRADES).values()
              if not t.get("test") and t["status"] in ("pending", "open", "closing")}
    after = lambda sym, when: when >= max(since, opened.get(sym, since))

    # 1. anything material about a stock the crew is in: always worth a hold-or-exit call
    held_alerts = []
    for sym in held:
        for item in groups.get(sym, []):
            key = f"news:{item['id']}"
            if after(sym, datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))) and key not in seen:
                held_alerts.append({"symbol": sym, "key": key, "source": "Benzinga", "at": item["created_at"],
                                    "headline": item["headline"], "url": item.get("url")})
    for entry in edgar.latest_8k(since=since, max_pages=2):
        sym = (tickers.get(entry["cik"]) or {}).get("ticker")
        filed = datetime.fromisoformat(entry["filed_at"].replace("Z", "+00:00"))
        if sym in held and after(sym, filed) and entry["accession"] not in seen and not entry["amendment"]:
            held_alerts.append({"symbol": sym, "key": entry["accession"], "source": "SEC 8-K",
                                "at": entry["filed_at"], "headline": f"8-K filed by {entry['company']}",
                                "url": entry["index_url"]})

    # 2. new heavy events that passed every SCOUT filter and have not moved yet
    hours = (now - since).total_seconds() / 3600
    skip = learning.seen_filings() | seen
    recent = learning.recently_judged_tickers() | held
    found = find_candidates(edgar, data.last_price, data.daily_bars, limit=5, since_hours=hours, log=log,
                            skip=skip, recent_tickers=recent, news=groups,
                            move_of=lambda t, s: move_since(data, t, s, now))
    found += find_news_candidates(edgar, data, hours, log=log, skip=skip, recent_tickers=recent, now=now,
                                  groups=groups)
    heavy, names = [], set()
    for c in sorted(found, key=lambda c: c.weight, reverse=True):
        seen.add(c.accession)  # judged by the watcher either way; the window moves on
        if c.weight >= 3 and c.ticker not in names:
            heavy.append(c)
            names.add(c.ticker)
    return heavy, held_alerts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=110)
    ap.add_argument("--dry-poll", type=float, metavar="MINUTES",
                    help="one look back over MINUTES, at any hour; prints what it would alert, writes nothing")
    args = ap.parse_args()
    if args.status:
        print("watching" if alive() else "not running")
        return 0
    if args.dry_poll:
        edgar, data, broker = Edgar(), AlpacaData(), PaperBroker()
        now = datetime.now(timezone.utc)
        raw = {i["id"]: i for i in fetch_news(data, now - HISTORY, now)}
        heavy, held_alerts = poll(edgar, data, broker, edgar.tickers(), raw, set(), now - timedelta(minutes=args.dry_poll), now)
        print(f"held: {sorted(held_symbols(broker))}")
        for c in heavy[:3]:
            print(f"  would alert, new event: {c.ticker} (weight {c.weight}) {c.source_excerpt.splitlines()[0][:110]}")
        for a in held_alerts:
            print(f"  would alert, held {a['symbol']}: {a['source']} {a['at']} {a['headline'][:110]}")
        if not heavy and not held_alerts:
            print("  nothing to alert")
        return 0
    if alive():
        print("ALREADY RUNNING")
        return 0
    started = now = datetime.now(timezone.utc)
    if not in_regular_session(now):
        print("CLOSED: outside regular hours, nothing to watch")
        return 0

    edgar, data, broker = Edgar(), AlpacaData(), PaperBroker()
    tickers = edgar.tickers()
    seen: set[str] = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    raw = {i["id"]: i for i in fetch_news(data, now - HISTORY, now)}
    last = start_from(now)
    log(f"start: {len(raw)} headlines of history, watching from {last.isoformat(timespec='seconds')}")

    while True:
        now = datetime.now(timezone.utc)
        if not in_regular_session(now):
            beat("stopped", last, started)
            print("CLOSED: the regular session has ended")
            return 0
        if now - started > timedelta(minutes=args.max_minutes):
            beat("stopped", last, started)
            print(f"TIMEOUT after {args.max_minutes:.0f} minutes; start the watcher again while the market is open")
            return 0
        beat("watching", last, started)
        since = last - OVERLAP
        try:
            heavy, held_alerts = poll(edgar, data, broker, tickers, raw, seen, since, now)
        except Exception as e:  # a feed hiccup must not kill a watch that has hours to run
            log(f"poll failed: {e!r}")
            time.sleep(POLL_SECONDS)
            continue

        if heavy or held_alerts:
            stamp = now.astimezone(NY).strftime("%Y%m%d-%H%M")
            root = RUNS_DIR / f"watch-{stamp}"
            if heavy:
                save_candidates(root, heavy[:3])
            if held_alerts:
                root.mkdir(parents=True, exist_ok=True)
                (root / "held.json").write_text(json.dumps(held_alerts, indent=2))
            seen.update(a["key"] for a in held_alerts)
            SEEN.write_text(json.dumps(sorted(seen)[-5000:]))
            beat("stopped", now, started)
            print(f"ALERT {root}")
            for c in heavy[:3]:
                print(f"  new event: {c.ticker} (weight {c.weight}) {c.source_excerpt.splitlines()[0][:110]}")
            for a in held_alerts:
                print(f"  held {a['symbol']}: {a['source']} {a['at']} {a['headline'][:110]}")
            return 0

        SEEN.write_text(json.dumps(sorted(seen)[-5000:]))
        last = now
        time.sleep(max(1.0, POLL_SECONDS - (datetime.now(timezone.utc) - now).total_seconds()))


if __name__ == "__main__":
    sys.exit(main())

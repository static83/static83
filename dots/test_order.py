#!/usr/bin/env python3
"""Send one small paper bracket order to check the whole order path: entry, stop, target, time limit.

    python test_order.py            # 1 share of F, stop and target 3% away, closed after 1 trading day
    python test_order.py SPY 1 2    # symbol, qty, time limit in trading days

The trade is recorded in runs/trades.json marked as a test, so the nightly review settles it like
any other, but it is kept out of the crew's scorecard and never blocks a real case.
"""
from __future__ import annotations

import sys

from crew import learning
from crew.broker import PaperBroker
from crew.market import AlpacaData


def main() -> int:
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "F"
    qty = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    horizon = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    broker = PaperBroker()
    if broker.mode != "alpaca-paper":
        print(f"broker mode is {broker.mode}: paper keys missing, nothing sent")
        return 2
    price = AlpacaData().last_price(symbol)
    if not price:
        print(f"no price for {symbol}")
        return 2
    # entry a little above the last price so it fills at the open; legs 3% either side of the entry
    entry = round(price * 1.01, 2)
    stop, target = round(entry * 0.97, 2), round(entry * 1.03, 2)
    order = broker.bracket_order(symbol, "buy", qty, entry, stop, target)
    learning.record_test_trade(order, horizon)
    print(f"sent {order['status']}: buy {qty} {symbol} limit {entry:.2f} stop {stop:.2f} target {target:.2f}, "
          f"time limit {horizon} trading day(s), order {order['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

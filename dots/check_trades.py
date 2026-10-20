#!/usr/bin/env python3
"""The 09:45 check: settle open paper trades right after the open, without running the crew.

Records entries that filled at the open, moves their stop and target to the price actually paid,
and picks up any stop, target or time-limit exit. Prints one line per change, or nothing changed.
"""
from __future__ import annotations

import sys

from crew import learning
from crew.broker import PaperBroker


def main() -> int:
    broker = PaperBroker()
    if broker.mode != "alpaca-paper":
        print(f"broker mode is {broker.mode}: paper keys missing, nothing checked")
        return 2
    lines: list[str] = []
    learning.review_trades(broker, lines.append)
    for line in lines:
        print(line)
    if not lines:
        print("nothing changed")
    print(f"crew balance {broker.equity():,.2f} USD")
    return 0


if __name__ == "__main__":
    sys.exit(main())

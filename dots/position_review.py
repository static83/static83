#!/usr/bin/env python3
"""Record the crew's hold-or-exit call on news about a stock it holds or is waiting to buy.

    python position_review.py SMCI hold "guidance unchanged, nothing new" "Supermicro files 8-K: ..."
    python position_review.py SMCI exit "auditor resigned: thesis rests on unaudited numbers" "..."

exit on a filled trade cancels its stop and target and sells at market (exit reason 'crew exit');
on an entry that has not filled, it cancels the order. Every call is kept in runs/trades.json.
"""
from __future__ import annotations

import sys

from crew import learning
from crew.broker import PaperBroker


def main() -> int:
    if len(sys.argv) != 5 or sys.argv[2] not in ("hold", "exit"):
        print(__doc__)
        return 2
    symbol, decision, reason, headline = sys.argv[1].upper(), sys.argv[2], sys.argv[3], sys.argv[4]
    broker = PaperBroker()
    if broker.mode != "alpaca-paper":
        print(f"broker mode is {broker.mode}: paper keys missing, nothing done")
        return 2
    t = learning.review_position(broker, symbol, decision, reason, headline)
    return 0 if t is not None else 1


if __name__ == "__main__":
    sys.exit(main())

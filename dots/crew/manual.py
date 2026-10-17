"""Manual mode: a human or an outside agent writes each memo by hand.

The Spider runs until it needs a memo that does not exist yet. It writes the case packet to
<dir>/outbox/<key>.json and stops. Write the memo to <dir>/inbox/<key>.json and run again;
the Spider replays every earlier memo from the inbox and continues. Candidates are cached in
<dir>/candidates.json so every rerun sees the same night.
"""
from __future__ import annotations

import json
from pathlib import Path

from .schema import Candidate, Memo


class NeedMemo(Exception):
    def __init__(self, key: str, path: Path):
        super().__init__(key)
        self.key, self.path = key, path


class ManualAsker:
    def __init__(self, root: Path):
        self.root = root
        (root / "inbox").mkdir(parents=True, exist_ok=True)
        (root / "outbox").mkdir(parents=True, exist_ok=True)
        self.seen: dict[tuple[int, str], int] = {}

    def __call__(self, role: str, packet: dict) -> Memo:
        case = packet["case"]
        n = self.seen.get((case, role), 0) + 1
        self.seen[(case, role)] = n
        key = f"case{case:02d}_{role}_{n}"
        memo_path = self.root / "inbox" / f"{key}.json"
        if memo_path.exists():
            memo = Memo.model_validate_json(memo_path.read_text())
            memo.role = role
            return memo
        out = self.root / "outbox" / f"{key}.json"
        out.write_text(json.dumps({"role": role, "packet": packet}, indent=2, default=str))
        raise NeedMemo(key, out)


def load_candidates(root: Path) -> list[Candidate] | None:
    p = root / "candidates.json"
    if not p.exists():
        return None
    return [Candidate.model_validate(c) for c in json.loads(p.read_text())]


def save_candidates(root: Path, cands: list[Candidate]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "candidates.json").write_text(json.dumps([c.model_dump() for c in cands], indent=2))


class OnceBroker:
    """Wraps the paper broker so a replayed case never sends the same order twice."""

    def __init__(self, broker, root: Path):
        self.broker, self.path = broker, root / "orders.json"
        self.sent = json.loads(self.path.read_text()) if self.path.exists() else {}

    def __getattr__(self, name):
        return getattr(self.broker, name)

    def bracket_order(self, symbol, side, qty, entry, stop, target):
        key = f"{symbol}:{side}:{qty}:{entry:.2f}:{stop:.2f}:{target:.2f}"
        if key in self.sent:
            return self.sent[key]
        order = self.broker.bracket_order(symbol, side, qty, entry, stop, target)
        self.sent[key] = order
        self.path.write_text(json.dumps(self.sent, indent=2))
        return order

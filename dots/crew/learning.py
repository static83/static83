"""The crew's memory: what it decided, what happened next, and what that says about each role.

Three files under runs/ (git-ignored):
  casebook.json  one record per case, traded or not, with forward returns filled in as they mature
  trades.json    one record per paper order, from entry to exit, with realized P&L
  lessons.md     the scorecard in plain words, rewritten every night

Nothing here trains a model. Each night the scorecard goes into every role's packet as
`track_record`, so the roles argue with their own history in front of them.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from statistics import mean
from typing import Optional
from zoneinfo import ZoneInfo

from .config import RUNS_DIR
from .market import bars_since
from .schema import Candidate, CaseResult, Memo

CASEBOOK = RUNS_DIR / "casebook.json"
TRADES = RUNS_DIR / "trades.json"
LESSONS = RUNS_DIR / "lessons.md"
HORIZONS = (1, 5, 20)
DEFAULT_HORIZON_DAYS = 10
MIN_SAMPLE = 10  # below this, the scorecard says the numbers are too few to act on
NY = ZoneInfo("America/New_York")


# ---- storage ------------------------------------------------------------------------------
def _load(path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path, data: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


# ---- what a case decided ------------------------------------------------------------------
def _last(memos: list[Memo], roles: tuple[str, ...], attr: str):
    for m in reversed(memos):
        if m.role in roles and getattr(m, attr, None) not in (None, "none"):
            return getattr(m, attr)
    return None


def ended_by(result: CaseResult) -> str:
    """Which part of the process decided the case."""
    if result.outcome == "paper_order":
        return "TRADED"
    if result.outcome == "restarted":
        return "RESTART"
    if result.outcome == "rejected":
        return "SPIDER"
    for m in reversed(result.memos):
        if m.role != "JOURNAL" and m.verdict == "object":
            return m.role
    return "ROUND_CAP"


def horizon_of(memos: list[Memo]) -> int:
    return int(_last(memos, ("QUANT", "ANALYST"), "horizon_days") or DEFAULT_HORIZON_DAYS)


def record_case(night: str, result: CaseResult, cand: Candidate, decided_at: datetime) -> None:
    book = _load(CASEBOOK)
    key = f"{night}-{result.case:02d}-{cand.ticker}"
    quant = next((m for m in reversed(result.memos) if m.role == "QUANT"), None)
    old = book.get(key, {})
    book[key] = {
        **old,
        "key": key,
        "night": night,
        "ticker": cand.ticker,
        "accession": cand.accession,
        "also_accessions": cand.also_accessions,
        "filed_at": cand.filed_at,
        "decided_at": decided_at.isoformat(),
        "price_at_decision": cand.last_price,
        "outcome": result.outcome,
        "ended_by": ended_by(result),
        "rounds": result.rounds,
        "objections": sum(len(m.objections) for m in result.memos),
        "direction": _last(result.memos, ("RISK", "QUANT", "ANALYST"), "direction"),
        "horizon_days": horizon_of(result.memos),
        "entry": quant.entry_price if quant else None,
        "stop": quant.stop_price if quant else None,
        "target": quant.target_price if quant else None,
        "p_strike": quant.p_strike if quant else None,
        "order_id": (result.order or {}).get("id"),
        "returns": old.get("returns", {}),
        "first_exit": old.get("first_exit"),
    }
    _save(CASEBOOK, book)


def record_trade(case_key: str, order: dict, memos: list[Memo]) -> None:
    if not order or order.get("status") == "not_sent" or not order.get("id"):
        return
    trades = _load(TRADES)
    if order["id"] in trades:
        return
    trades[order["id"]] = {
        "order_id": order["id"],
        "case_key": case_key,
        "symbol": order["symbol"],
        "side": order["side"],
        "qty": int(order["qty"]),
        "entry_limit": float(order["limit_price"]),
        "stop": float(order["stop_loss"]["stop_price"]),
        "target": float(order["take_profit"]["limit_price"]),
        "horizon_days": horizon_of(memos),
        "status": "pending",
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(TRADES, trades)


def record_test_trade(order: dict, horizon_days: int) -> None:
    """A plumbing check placed by test_order.py: settled like any trade, kept out of the crew's record."""
    record_trade("test-order-path", order, [])
    trades = _load(TRADES)
    if order.get("id") in trades:
        trades[order["id"]].update(test=True, horizon_days=horizon_days)
        _save(TRADES, trades)


def test_symbols() -> set[str]:
    """Symbols held or ordered only by an unfinished test trade; the one-position rule ignores them."""
    return {t["symbol"] for t in _load(TRADES).values() if t.get("test") and t["status"] in ("pending", "open", "closing")}


def seen_filings() -> set[str]:
    """Accession numbers and ticker|filed_at pairs the crew has already judged."""
    out: set[str] = set()
    for r in _load(CASEBOOK).values():
        if r.get("accession"):
            out.add(r["accession"])
        out.update(r.get("also_accessions") or [])
        out.add(f"{r['ticker']}|{r['filed_at']}")
    return out


def recently_judged_tickers(days: int = 3) -> set[str]:
    """Companies the crew has judged in the last few days; their news is usually the same story."""
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return {r["ticker"] for r in _load(CASEBOOK).values() if r.get("decided_at", "") >= cutoff}


# ---- rule 8: the time limit ---------------------------------------------------------------
def _pnl(side: str, qty: int, entry: float, exit_: float) -> float:
    sign = 1 if side == "buy" else -1
    return round(sign * (exit_ - entry) * qty, 2)


def review_trades(broker, log) -> list[dict]:
    """Update every open trade; close any that has outlived its holding period.

    `log(text)` writes to the night's ledger. Returns the trades that changed tonight.
    """
    trades = _load(TRADES)
    changed = []
    today = datetime.now(NY).date().isoformat()
    for t in trades.values():
        if t["status"] in ("closed", "unfilled"):
            continue
        parent = broker.get_order(t["order_id"])
        if parent is None:
            continue
        filled = float(parent.get("filled_qty") or 0)
        if t["status"] == "pending":
            if filled == 0:
                if parent.get("status") in ("canceled", "expired", "rejected"):
                    t["status"] = "unfilled"
                    changed.append(t)
                    log(f"{t['symbol']}: entry never filled, closed out")
                continue
            t.update(status="open", fill_price=float(parent["filled_avg_price"]), filled_at=parent["filled_at"])
            changed.append(t)
            log(f"{t['symbol']}: entry filled {t['qty']} @ {t['fill_price']:.2f}")
            _reanchor(broker, t, parent, log)

        # A bracket leg that filled means the stop or the target did its job.
        for leg in parent.get("legs") or []:
            if leg.get("status") == "filled" and t["status"] == "open":
                exit_price = float(leg["filled_avg_price"])
                reason = "target" if leg.get("type") == "limit" else "stop"
                t.update(status="closed", exit_price=exit_price, exit_reason=reason, closed_at=leg["filled_at"],
                         pnl=_pnl(t["side"], t["qty"], t["fill_price"], exit_price))
                changed.append(t)
                log(f"{t['symbol']}: {reason} hit @ {exit_price:.2f}, P&L {t['pnl']:+.2f}")

        if t["status"] == "closing" and not t.get("close_order_id"):
            close = broker.close_position(t["symbol"])  # last night's close was refused; try again
            t["close_order_id"] = (close or {}).get("id")
            log(f"{t['symbol']}: retrying {t.get('close_reason', 'time limit')} close" + ("" if close else ", refused again"))

        if t["status"] == "closing" and t.get("close_order_id"):
            close = broker.get_order(t["close_order_id"])
            if close and close.get("status") == "filled":
                exit_price = float(close["filled_avg_price"])
                reason = t.get("close_reason", "time limit")
                t.update(status="closed", exit_price=exit_price, exit_reason=reason, closed_at=close["filled_at"],
                         pnl=_pnl(t["side"], t["qty"], t["fill_price"], exit_price))
                changed.append(t)
                log(f"{t['symbol']}: {reason} exit filled @ {exit_price:.2f}, P&L {t['pnl']:+.2f}")

        if t["status"] == "open":
            held = broker.trading_days_between(t["filled_at"][:10], today)
            t["days_held"] = held
            if held >= t["horizon_days"]:
                _close_now(broker, t, parent, "time limit")
                changed.append(t)
                log(f"{t['symbol']}: held {held} of {t['horizon_days']} trading days, time limit hit, closing at the next open")
    _save(TRADES, trades)
    return changed


def _close_now(broker, t: dict, parent: dict, reason: str) -> None:
    """Cancel the bracket's stop and target, then flatten at market (queued for the open if closed)."""
    for leg in parent.get("legs") or []:
        if leg.get("status") in ("new", "accepted", "held", "pending_new"):
            broker.cancel_order(leg["id"])
    close = broker.close_position(t["symbol"])
    t.update(status="closing", close_order_id=(close or {}).get("id"), close_reason=reason)


def review_position(broker, symbol: str, decision: str, reason: str, headline: str, log=print) -> Optional[dict]:
    """The crew's call on news about a stock it holds or is waiting to buy: hold, or exit now.

    An exit on a filled trade cancels the stop and target and sells at market ('crew exit'); on an
    entry that has not filled, it cancels the order. Every call is kept in the trade's reviews.
    """
    if decision not in ("hold", "exit"):
        raise ValueError("decision must be hold or exit")
    review_trades(broker, log)  # sync first: the entry may have filled since the last check
    trades = _load(TRADES)
    t = next((t for t in trades.values() if t["symbol"] == symbol and not t.get("test")
              and t["status"] in ("pending", "open")), None)
    if t is None:
        log(f"{symbol}: no open crew trade to review")
        return None
    t.setdefault("reviews", []).append({"at": datetime.now(timezone.utc).isoformat(), "headline": headline,
                                        "decision": decision, "reason": reason})
    if decision == "exit":
        if t["status"] == "pending":
            ok = broker.cancel_order(t["order_id"])
            log(f"{symbol}: crew cancelled the unfilled entry ({reason})" + ("" if ok else ", cancel refused"))
        else:
            _close_now(broker, t, broker.get_order(t["order_id"]) or {}, "crew exit")
            log(f"{symbol}: crew exit, selling {t['qty']} at market ({reason})")
    else:
        log(f"{symbol}: crew holds ({reason})")
    _save(TRADES, trades)
    return t


def _reanchor(broker, t: dict, parent: dict, log) -> None:
    """Keep the stop and target the planned distance from the price actually paid.

    A limit entry fills at its limit or better, so a fill below a long's limit leaves less room to
    the stop than QUANT planned. Shift both legs by the gap; the dollar risk stays what RISK sized."""
    gap = round(t["fill_price"] - t["entry_limit"], 2)
    if abs(gap) < 0.01:
        return
    moved = {}
    for leg in parent.get("legs") or []:
        if leg.get("status") not in ("new", "accepted", "held", "pending_new"):
            continue  # already filled or cancelled; nothing left to move
        if leg.get("type") == "stop" and leg.get("stop_price"):
            new = round(float(leg["stop_price"]) + gap, 2)
            if broker.replace_order(leg["id"], stop_price=new):
                moved["stop"] = new
        elif leg.get("type") == "limit" and leg.get("limit_price"):
            new = round(float(leg["limit_price"]) + gap, 2)
            if broker.replace_order(leg["id"], limit_price=new):
                moved["target"] = new
    if moved:
        t.update(moved, reanchored_by=gap)
        log(f"{t['symbol']}: filled {gap:+.2f} from the planned entry, moved "
            + ", ".join(f"{k} to {v:.2f}" for k, v in moved.items()))


# ---- what happened next, for every case ---------------------------------------------------
def _filed_date(filed_at: str) -> str:
    try:
        return datetime.fromisoformat(filed_at.replace("Z", "+00:00")).astimezone(NY).date().isoformat()
    except ValueError:
        return filed_at[:10]


def _first_open_after(when: str) -> str:
    """Date of the first 9:30 New York open after `when`: where the crew's entry would have filled.

    A morning decision (before 9:30) enters that day; a night decision enters the next day.
    Weekends roll forward because bars only exist on trading days.
    """
    from datetime import timedelta

    t = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(NY)
    open_today = t.replace(hour=9, minute=30, second=0, microsecond=0)
    day = t.date() if t < open_today else t.date() + timedelta(days=1)
    return day.isoformat()


def update_outcomes(data) -> int:
    """Fill in forward returns for every case as the days pass. Returns how many records changed."""
    book = _load(CASEBOOK)
    changed = 0
    for rec in book.values():
        if all(str(h) in rec.get("returns", {}) for h in HORIZONS) and rec.get("first_exit"):
            continue
        filed = _filed_date(rec["filed_at"])
        entry_day = _first_open_after(rec.get("decided_at") or rec["filed_at"])
        bars = [b for b in bars_since(data, rec["ticker"], filed) if b["t"][:10] >= entry_day]
        if not bars:
            continue
        base = float(bars[0]["o"])  # the first open after the filing: where an entry would have filled
        sign = {"long": 1, "short": -1}.get(rec.get("direction") or "", 0)
        before = json.dumps(rec, sort_keys=True, default=str)
        for h in HORIZONS:
            if len(bars) >= h and base:
                raw = float(bars[h - 1]["c"]) / base - 1
                rec["returns"][str(h)] = {"raw": round(raw, 4), "thesis": round(sign * raw, 4) if sign else None}
        if rec.get("stop") and rec.get("target") and sign and not rec.get("first_exit"):
            for i, b in enumerate(bars[: rec["horizon_days"]]):
                lo, hi = float(b["l"]), float(b["h"])
                hit_stop = lo <= rec["stop"] if sign > 0 else hi >= rec["stop"]
                hit_target = hi >= rec["target"] if sign > 0 else lo <= rec["target"]
                if hit_stop:  # if both print on the same day, assume the stop came first
                    rec["first_exit"] = "stop"
                    break
                if hit_target:
                    rec["first_exit"] = "target"
                    break
            else:
                if len(bars) >= rec["horizon_days"]:
                    rec["first_exit"] = "time"
        if json.dumps(rec, sort_keys=True, default=str) != before:
            changed += 1
    _save(CASEBOOK, book)
    return changed


# ---- the scorecard ------------------------------------------------------------------------
def _pct(x: float) -> str:
    return f"{100 * x:+.1f}%"


def _caveat(n: int) -> str:
    return "" if n >= MIN_SAMPLE else f" Only {n} so far, too few to act on."


def scorecard() -> dict:
    book = list(_load(CASEBOOK).values())
    every = list(_load(TRADES).values())
    trades = [t for t in every if not t.get("test")]
    lines: list[str] = []

    for role, label in (("SCOUT", "SCOUT skipped"), ("SKEPTIC", "SKEPTIC killed"), ("QUANT", "QUANT rejected"),
                        ("RISK", "RISK vetoed"), ("ROUND_CAP", "The round cap ended"), ("SPIDER", "The Spider blocked")):
        cases = [r for r in book if r["ended_by"] == role]
        scored = [r["returns"]["5"]["thesis"] for r in cases if r["returns"].get("5", {}).get("thesis") is not None]
        if not cases:
            continue
        if not scored:
            lines.append(f"{label} {len(cases)} case(s); none has a thesis direction with 5 trading days of history yet.")
            continue
        wins = sum(1 for x in scored if x > 0)
        lines.append(
            f"{label} {len(scored)} case(s) with a thesis. Five days later the thesis averaged {_pct(mean(scored))}, "
            f"right {wins} of {len(scored)} times. Positive means this gate is stopping trades that would have worked."
            + _caveat(len(scored))
        )

    closed = [t for t in trades if t["status"] == "closed"]
    if closed:
        pnl = sum(t["pnl"] for t in closed)
        wins = sum(1 for t in closed if t["pnl"] > 0)
        by = {k: sum(1 for t in closed if t.get("exit_reason") == k) for k in ("target", "stop", "time limit", "crew exit")}
        lines.append(
            f"Trades closed: {len(closed)}, realized P&L {pnl:+.2f} USD, {wins} winners. "
            f"Exits: {by['target']} at target, {by['stop']} at stop, {by['time limit']} on the time limit, "
            f"{by['crew exit']} by the crew on news." + _caveat(len(closed))
        )
    open_ = [t for t in trades if t["status"] in ("pending", "open", "closing")]
    if open_:
        lines.append("Still open: " + ", ".join(f"{t['symbol']} ({t['status']})" for t in open_) + ".")

    for t in (t for t in every if t.get("test")):
        lines.append(f"Test order (plumbing check, not a crew call): {t['side']} {t['qty']} {t['symbol']}, {t['status']}"
                     + (f", {t['exit_reason']} exit, P&L {t['pnl']:+.2f}" if t["status"] == "closed" else "") + ".")

    calib = [r for r in book if r.get("p_strike") is not None and r.get("first_exit") in ("stop", "target", "time")]
    if calib:
        predicted = mean(r["p_strike"] for r in calib)
        actual = sum(1 for r in calib if r["first_exit"] == "stop") / len(calib)
        lines.append(
            f"QUANT calibration: predicted stop-out {100 * predicted:.0f}% on average, actual {100 * actual:.0f}% "
            f"across {len(calib)} case(s)." + _caveat(len(calib))
        )

    if not lines:
        lines.append("No history yet. This is one of the crew's first nights.")

    recent = sorted(book, key=lambda r: r["decided_at"], reverse=True)[:8]
    return {
        "how_to_use": "Evidence about this crew's own past calls. Weigh it by sample size; never treat a handful of cases as a rule.",
        "scorecard": lines,
        "recent_cases": [
            {
                "ticker": r["ticker"],
                "night": r["night"],
                "ended_by": r["ended_by"],
                "direction": r.get("direction"),
                "thesis_5d": r["returns"].get("5", {}).get("thesis"),
                "raw_5d": r["returns"].get("5", {}).get("raw"),
                "first_exit": r.get("first_exit"),
            }
            for r in recent
        ],
        "cases_on_file": len(book),
    }


def write_lessons(card: dict) -> None:
    stamp = datetime.now(NY).strftime("%Y-%m-%d %H:%M ET")
    body = [f"# DOTS track record", "", f"Updated {stamp}. {card['cases_on_file']} case(s) on file.", ""]
    body += [f"- {line}" for line in card["scorecard"]]
    LESSONS.parent.mkdir(exist_ok=True)
    LESSONS.write_text("\n".join(body) + "\n")

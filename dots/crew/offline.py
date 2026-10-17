"""Canned memos so the pipeline, ledger and dashboard can be exercised with no API keys.

The script follows the night in the README: case 01 dies at RISK, case 02 comes back smaller
and gets a paper order. Nothing here is a prediction of anything.
"""
from __future__ import annotations

from datetime import timedelta

from .schema import Candidate, Memo

SAMPLE_FILINGS = [
    Candidate(
        ticker="EXMP", company="Example Industrial Corp", cik="0000000001", accession="0000000001-26-000001",
        items=["1.01", "8.01"], filed_at="2026-10-06T21:48:00Z",
        url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K",
        source_excerpt="Item 1.01 Entry into a Material Definitive Agreement. On October 6, 2026 the Company "
        "entered into a supply agreement with a customer representing approximately 18% of trailing revenue, "
        "extending the term through 2031 with minimum annual volumes...", market_cap_usd=2.1e9, last_price=41.20,
    ),
    Candidate(
        ticker="SMPL", company="Sample Logistics Inc", cik="0000000002", accession="0000000002-26-000002",
        items=["5.02"], filed_at="2026-10-07T01:12:00Z",
        url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K",
        source_excerpt="Item 5.02 Departure of Directors or Certain Officers. On October 6, 2026 the Chief "
        "Financial Officer notified the Company of his resignation effective immediately to pursue other "
        "opportunities. The Company has appointed its Controller as interim CFO...", market_cap_usd=640e6, last_price=12.85,
    ),
]

SCRIPT = {
    1: {
        "SCOUT": Memo(role="SCOUT", verdict="agree", headline="18% customer locked through 2031. Press release beat the filing by 40 min",
                      reasoning=["Filing quotes minimum annual volumes", "PR hit the wire at 21:08, filing at 21:48"],
                      objections=["SCOUT: public 40 minutes before the filing"]),
        "ANALYST": Memo(role="ANALYST", verdict="agree", headline="Revenue floor through 2031 lowers the discount rate, not the growth",
                        reasoning=["Thesis is multiple expansion, not estimate revision", "Horizon 10 days"],
                        objections=["SCOUT: public 40 minutes before the filing (open)"], direction="long"),
        "SKEPTIC": Memo(role="SKEPTIC", verdict="agree", headline="Obvious, late, and the after-hours tape already moved 6%",
                        reasoning=["Anyone with a filing alert traded this at 21:09", "Multiple expansion thesis is unfalsifiable in 10 days"],
                        objections=["SCOUT: public 40 minutes before the filing (open)", "SKEPTIC: 6% after-hours move already in the price", "SKEPTIC: thesis has no 10-day test"],
                        priced_in_score=0.8),
        "QUANT": Memo(role="QUANT", verdict="agree", headline="Entry 43.70, stop 41.90, target 47.00, bad exit costs $3.10 on $37",
                      reasoning=["Spread 4c, slippage 5 bps", "p_strike 0.46 from 30-day gap distribution"],
                      objections=["SCOUT: public 40 minutes before the filing (open)", "SKEPTIC: 6% after-hours move already in the price (open)", "SKEPTIC: thesis has no 10-day test (open)"],
                      direction="long", entry_price=43.70, stop_price=41.90, target_price=47.00, position_size_usd=37.0,
                      fee_estimate_usd=0.04, bad_exit_cost_usd=3.10, p_strike=0.46),
        "RISK": Memo(role="RISK", verdict="object", headline="Full account on one name after a 6% gap. No.",
                     reasoning=["Bad exit is 8.4% of the account", "p_strike 0.46 with 3 open objections is a coin flip with fees"],
                     objections=["SCOUT: public 40 minutes before the filing (open)", "SKEPTIC: 6% after-hours move already in the price (open)", "SKEPTIC: thesis has no 10-day test (open)", "RISK: size exceeds the bad-exit cap"]),
        "JOURNAL": Memo(role="JOURNAL", verdict="agree", headline="Case 01: no trade. Four objections, none answered.",
                        reasoning=["Dropped at RISK", "Check tomorrow: did EXMP hold the 6% gap"]),
    },
    2: {
        "SCOUT": Memo(role="SCOUT", verdict="agree", headline="CFO out effective immediately, controller interim, no PR before filing",
                      reasoning=["Filing is the first public mention", "Item 5.02 only, no 2.02 attached"], objections=[]),
        "ANALYST": Memo(role="ANALYST", verdict="agree", headline="Sudden CFO exit before Q3 print: short, 5-day horizon",
                        reasoning=["'Effective immediately' plus internal interim is the pattern before restatements", "Wrong if the 10-Q lands clean on the 28th"],
                        objections=[], direction="short"),
        "SKEPTIC": Memo(role="SKEPTIC", verdict="send_back", headline="Base rate for 'effective immediately' CFO exits is not in the memo. Get it.",
                        reasoning=["Pattern-matching one filing to restatements is a story, not a rate"],
                        objections=["SKEPTIC: no base rate for the pattern"], send_back_to="ANALYST", priced_in_score=0.2),
        "ANALYST#2": Memo(role="ANALYST", verdict="agree", headline="Base rate added: 1 in 4 such exits precede a negative surprise within 60 days",
                          reasoning=["Rate is from the crew's own filing log, not a vendor", "Horizon stays 5 days"],
                          objections=["SKEPTIC: no base rate for the pattern (answered: 1 in 4)"], direction="short"),
        "SKEPTIC#2": Memo(role="SKEPTIC", verdict="agree", headline="One in four is a bet, not a thesis. Objection stays on record.",
                          reasoning=["Fresh information, nobody has traded it yet", "Still three-to-one it is nothing"],
                          objections=["SKEPTIC: no base rate for the pattern (answered: 1 in 4)", "SKEPTIC: 75% chance this is noise (open)"], priced_in_score=0.15),
        "QUANT": Memo(role="QUANT", verdict="agree", headline="Short 12.60, stop 13.30, target 11.40, bad exit $0.95 on $12 size",
                      reasoning=["Spread 2c", "p_strike 0.38", "EV positive only below $14 size after fees"],
                      objections=["SKEPTIC: no base rate for the pattern (answered: 1 in 4)", "SKEPTIC: 75% chance this is noise (open)"],
                      direction="short", entry_price=12.60, stop_price=13.30, target_price=11.40, position_size_usd=12.6,
                      fee_estimate_usd=0.02, bad_exit_cost_usd=0.95, p_strike=0.38),
        "RISK": Memo(role="RISK", verdict="agree", headline="One share. Bad exit 2.6% of account. Approved.",
                     reasoning=["Size 12.60 = one share", "Stop is outside the 30-day max gap"],
                     objections=["SKEPTIC: no base rate for the pattern (answered: 1 in 4)", "SKEPTIC: 75% chance this is noise (open)"],
                     direction="short", position_size_usd=12.6, stop_price=13.30),
        "JOURNAL": Memo(role="JOURNAL", verdict="agree", headline="Case 02: one share short, one objection still open.",
                        reasoning=["Trade exists because SKEPTIC's send-back was answered", "Check tomorrow: 10-Q date and any 8-K/A"]),
    },
}


class OfflineAsker:
    """Returns the scripted memos in order and advances a fake clock so the replay has a timeline."""

    def __init__(self, clock):
        self.clock = clock
        self.seen: dict[tuple[int, str], int] = {}

    def __call__(self, role: str, packet: dict) -> Memo:
        case = packet["case"]
        n = self.seen.get((case, role), 0) + 1
        self.seen[(case, role)] = n
        key = role if n == 1 else f"{role}#{n}"
        self.clock.advance(timedelta(minutes=37 if role != "JOURNAL" else 12))
        memo = SCRIPT.get(case, SCRIPT[2]).get(key) or SCRIPT[2][role]
        return memo.model_copy()


class FakeClock:
    def __init__(self, start):
        self.now = start

    def advance(self, delta):
        self.now += delta

    def __call__(self):
        return self.now

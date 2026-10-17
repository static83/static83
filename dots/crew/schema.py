"""Shared data shapes. Memo is what every role returns; it is also the Claude output schema."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["SCOUT", "ANALYST", "SKEPTIC", "QUANT", "RISK", "JOURNAL"]
Verdict = Literal["agree", "object", "send_back"]
Stage = Literal["SCOUT", "ANALYST", "SKEPTIC", "QUANT", "RISK"]


class Candidate(BaseModel):
    """One 8-K filing SCOUT pulled, with the original source attached."""

    ticker: str
    company: str
    cik: str
    accession: str
    items: list[str]
    filed_at: str  # ISO timestamp from EDGAR
    url: str
    source_excerpt: str  # the filing text, trimmed, never paraphrased
    market_cap_usd: Optional[float] = None
    last_price: Optional[float] = None


class Memo(BaseModel):
    """What a role hands back to the Spider. Flat on purpose so the schema stays strict."""

    role: Role
    verdict: Verdict
    headline: str = Field(description="One line, under 90 characters, in the role's own voice.")
    reasoning: list[str] = Field(description="3-7 short bullets. Receipts, not vibes.")
    objections: list[str] = Field(
        default_factory=list,
        description="Every objection raised or carried forward. Never drop an earlier one.",
    )
    send_back_to: Optional[Stage] = Field(
        default=None, description="Only when verdict is send_back: the stage that must redo its work."
    )
    direction: Optional[Literal["long", "short", "none"]] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    position_size_usd: Optional[float] = None
    fee_estimate_usd: Optional[float] = None
    bad_exit_cost_usd: Optional[float] = None
    p_strike: Optional[float] = Field(
        default=None, description="QUANT: probability the position is stopped out before target, 0-1."
    )
    priced_in_score: Optional[float] = Field(
        default=None, description="SKEPTIC: 0 = fresh information, 1 = fully priced in."
    )


class Event(BaseModel):
    """One line in the ledger. The dashboard replays these."""

    t: str  # ISO timestamp
    minute: int  # minutes since the night started
    role: str
    kind: Literal["handoff", "objection", "send_back", "restart", "order", "report", "system"]
    case: int
    text: str


class CaseResult(BaseModel):
    case: int
    ticker: str
    outcome: Literal["no_trade", "restarted", "paper_order", "rejected"]
    rounds: int
    memos: list[Memo]
    order: Optional[dict] = None

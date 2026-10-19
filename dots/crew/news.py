"""SCOUT's second source: Benzinga news through Alpaca's market-data API.

Most of the feed is filler ("If you invested $1,000 ten years ago...", option-flow and ratio
pieces, price-target notes). Only headlines that name a material event are kept, and only for
one or two companies, so sector round-ups drop out.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Optional

# Headlines that name an event that changes something for the business.
_MATERIAL = re.compile(
    r"\b(halt(ed)?|resum(es|ed) trading|fda|approv(al|es|ed)|clearance|complete response|crl|"
    r"phase (1|2|3|i|ii|iii)|topline|trial (results|data|met|fail)|primary endpoint|"
    r"acquir(e|es|ed|ing)|acquisition|merger|to (buy|be acquired)|buyout|takeover|tender offer|go(ing)? private|"
    r"definitive agreement|strategic (review|alternatives)|spin[- ]?off|"
    r"contract|award(ed)?|wins? .{0,30}(deal|order|contract)|partnership|collaboration|licens(e|ing)|"
    r"guidance|(raises|lowers|cuts|withdraws|reaffirms) .{0,25}(outlook|forecast|guidance)|preliminary|"
    r"recall|lawsuit|sues|settle(s|ment)|investigation|probe|subpoena|charges|indict|"
    r"bankruptcy|chapter 11|default|delist(ed|ing)?|going concern|"
    r"(suspends|cuts|initiates|eliminates) .{0,20}dividend|special dividend|buyback|repurchase|"
    r"(ceo|cfo|chief executive|chief financial).{0,40}(resign|steps? down|depart|ousted|fired|retire|appoint|named)|"
    r"(resign|steps? down|depart|ousted|fired|retire|appoint|named).{0,40}(ceo|cfo|chief executive|chief financial)|"
    r"cyber(attack| ?security incident)|breach|outage|explosion|fire at)\b",
    re.IGNORECASE,
)
# Headlines that are commentary, not events, even when they contain a material word.
_FILLER = re.compile(
    r"if you (had )?invested|would have made|invested in .{0,40} ago|how much .{0,40} (worth|invested)|"
    r"\bp/?e ratio|price over earnings|short interest|options activity|unusual options|whale|"
    r"what'?s going on with|why is .{0,40} (stock|shares)|stocks? (moving|to watch)|top (gainers|losers)|movers|"
    r"price target|(upgrade|downgrade|initiat|reiterat|maintain)s?\b.{0,40}\b(analyst|rating|buy|sell|hold|overweight|underweight|outperform)|"
    r"analyst ratings?|expert ratings|earnings (preview|outlook)|what to expect|ahead of earnings|"
    r"a look (at|into)|peering into|insight into|analyzing|technical|52-week|market clubhouse|"
    r"premarket|pre-market|after-hours (movers|session)|mid-day|midday|crypto|bitcoin|etf|"
    r"^reported earlier|become\W{0,2}obvious|this week in|week in review|weekly recap|"
    r"\bsays? .{0,40}(has become|is|are) (obvious|likely|inevitable)|\bwarns? .{0,30}against|"
    r"\bto present\b|\bwill present\b|\bpresents?\b.{0,40}\bat\b|"
    r"\b(doses|dosed|dosing|enrolls|enrolled|enrollment)\b|first patient|last patient|\bon track\b|"
    r"(stockholders|shareholders)'? (approv|vote)|ipo security released",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(raw: Optional[str]) -> str:
    return _WS.sub(" ", html.unescape(_TAGS.sub(" ", raw or ""))).strip()


def classify(item: dict) -> Optional[str]:
    """Why a news item is not a case, or None if it is a material single-company event."""
    head = item.get("headline", "")
    symbols = item.get("symbols") or []
    if not symbols:
        return "no company tagged"
    if len(symbols) > 2:
        return "round-up of several companies"
    if _FILLER.search(head):
        return "commentary, not an event"
    if not _MATERIAL.search(head):
        return "no material event in the headline"
    return None


def fetch_news(data, since: datetime, until: Optional[datetime] = None, max_pages: int = 30) -> list[dict]:
    """All Benzinga items published since `since`, newest first, with full text where provided."""
    items, token = [], None
    for _ in range(max_pages):
        params = {"start": since.isoformat(), "limit": 50, "sort": "desc", "include_content": "true"}
        if until:
            params["end"] = until.isoformat()
        if token:
            params["page_token"] = token
        got = data._get("/v1beta1/news", **params) or {}
        items.extend(got.get("news") or [])
        token = got.get("next_page_token")
        if not token:
            break
    return items


def first_public_session(created_at: datetime):
    """The first regular session that could trade on news published at `created_at`."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    t = created_at.astimezone(ZoneInfo("America/New_York"))
    return t.date() if t.hour < 16 else t.date() + timedelta(days=1)

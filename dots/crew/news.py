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
    r"\b(trading halt|halt(ed|s)? (trading|pending|for news)|halted|resum(es|ed) trading|fda|approv(al|es|ed)|clearance|complete response|crl|"
    r"phase (1|2|3|i|ii|iii)|topline|trial (results|data|met|fail)|primary endpoint|"
    r"acquir(e|es|ed|ing)|acquisition|merger|to (buy|be acquired)|buyout|takeover|tender offer|go(ing)? private|"
    r"definitive agreement|strategic (review|alternatives)|spin[- ]?off|"
    r"contract|award(ed)?|wins? .{0,30}(deal|order|contract)|partnership|collaboration|licens(e|ing)|warrants?|"
    r"guidance|(raises|lowers|cuts|withdraws|reaffirms) .{0,25}(outlook|forecast|guidance)|preliminary (\w+ ){0,3}(results|revenue|sales|production|earnings|estimates?|figures|financial|q[1-4]|quarter)|"
    r"recall|lawsuit|sues|settle(s|ment)|investigation|probe|subpoena|charges|indict|"
    r"bankruptcy|chapter 11|default|delist(ed|ing)?|going concern|"
    r"(suspends|cuts|initiates|eliminates) .{0,20}dividend|special dividend|buyback|repurchase|"
    r"(ceo|cfo|chief executive|chief financial).{0,40}(resign|steps? down|depart|ousted|fired|retire|appoint|names?)|"
    r"(resign|steps? down|depart|ousted|fired|retire|appoint|names?).{0,40}(ceo|cfo|chief executive|chief financial)|"
    r"cyber(attack| ?security incident)|breach|outage|explosion|fire at)\b",
    re.IGNORECASE,
)
# Headlines that are commentary, not events, even when they contain a material word.
_FILLER = re.compile(
    r"if you (had )?invested|would have made|invested in .{0,40} ago|how much .{0,40} (worth|invested)|"
    r"\bp/?e ratio|price over earnings|short interest|options activity|unusual options|whale|"
    r"what'?s going on with|why (is|are|did|does) .{0,40} (stock|shares|warrants|trending|moving|up|down)|\bpredicts?\b|"
    r"\bcalls? for\b|\bexpects? .{0,30} to offer\b|benzinga (viewers|readers)|\bpoll(ed|s)?\b|\bsays? (bullish|bearish|top) analysts?\b|\banalysts? (says?|sees?|thinks?)\b|\bmini[- ]?tender\b|\bof the year\b|\bhonou?red\b|\brecogni[sz]ed (as|by|for)\b|\bawards? (winner|recipient)\b|^watching\b|\bshares (are )?trading (lower|higher)\b|\bshares (are )?(up|down|falling|rising|sliding|surging)\b|stocks? (moving|to watch)|top (gainers|losers)|movers|"
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
# FDA permission to start or widen a study: a step on the way, not a result.
_PROCEDURAL = re.compile(
    r"\b(ide|ind|investigational (device exemption|new drug))\b.{0,60}(clear|approv|supplement|accept)|"
    r"(clear|approv|accept)\w*.{0,60}\b(ide|ind|investigational (device exemption|new drug))\b",
    re.IGNORECASE,
)
# How much an event can move a stock, used to pick which cases the crew sees first.
_TIER3 = re.compile(
    r"\b(trading halt|halt(ed|s)? (trading|pending|for news)|halted|acquir(e|es|ed|ing)|acquisition|merger|to (buy|be acquired)|buyout|takeover|tender offer|"
    r"go(ing)? private|definitive agreement|strategic alternatives|fda approv\w*|approv(al|es|ed) .{0,30}(fda|ema)|"
    r"complete response|crl|topline|phase (3|iii)|primary endpoint|trial (results|data|met|fail)|"
    r"(raises|lowers|cuts|withdraws|boosts|slashes) .{0,25}(outlook|forecast|guidance)|preliminary (\w+ ){0,3}(results|revenue|sales|production|earnings|estimates?|figures|financial|q[1-4]|quarter)|"
    r"bankruptcy|chapter 11|going concern|delist(ed|ing)?|special dividend|"
    r"(ceo|chief executive).{0,40}(resign|steps? down|depart|ousted|fired|retire|appoint|names?)|"
    r"(resign|steps? down|depart|ousted|fired|retire|appoint|names?).{0,40}(ceo|chief executive))\b",
    re.IGNORECASE,
)
_TIER2 = re.compile(
    r"\b(contract|award(ed)?|wins?|order|settle(s|ment)|lawsuit|sues|investigation|probe|subpoena|charges|"
    r"indict|recall|buyback|repurchase|dividend|breach|cyber|outage|explosion|phase (2|ii)|warrants?|"
    r"cfo|chief financial|default|spin[- ]?off|strategic review|approv(al|es|ed))\b",
    re.IGNORECASE,
)
# Plaintiff-firm notices ('shareholder alert', 'investigating the merger'): they repeat old news to find clients.
_SOLICITATION = re.compile(
    r"law firm|class[- ]action (firm|lawsuit filed|deadline)|shareholder[- ]alert|investor[- ]alert|"
    r"\binvestigat\w* (the )?(merger|claims|on behalf)|deadline reminder|lead plaintiff",
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
    if _SOLICITATION.search(head) or _SOLICITATION.search(item.get("url") or ""):
        return "law-firm solicitation, not company news"
    if _PROCEDURAL.search(head):
        return "procedural FDA step (IDE/IND), not a result"
    if not _MATERIAL.search(head):
        return "no material event in the headline"
    return None


# Events whose size can be read off the headline: deals, contracts, settlements, buybacks.
_SIZED = re.compile(
    r"\b(acquir\w*|acquisition|merger|to buy|buys?|purchase\w*|contract|award\w*|wins?|order|deal|"
    r"settle\w*|buyback|repurchase|warrants?|investment|stake|units|partnership|collaboration)\b",
    re.IGNORECASE,
)
# Events that matter whatever their size, or where the company itself is the target.
_UNSIZED = re.compile(
    r"\b(fda|ema|approv\w*|topline|phase|endpoint|trial|guidance|outlook|forecast|preliminary (\w+ ){0,3}(results|revenue|sales|production|earnings|estimates?|figures|financial|q[1-4]|quarter)|trading halt|halted|"
    r"bankruptcy|chapter 11|going concern|delist\w*|ceo|chief executive|to be acquired|buyout|takeover|"
    r"tender offer|go(ing)? private|strategic (alternatives|review)|recall|breach|cyber\w*)\b",
    re.IGNORECASE,
)
# Buying a slice of another company: it matters to that company, hardly to the buyer.
_MINORITY = re.compile(
    r"\b(acquire|buy|take|purchase)s?\b.{0,30}\b\d+(\.\d+)?% (stake|of|interest)|minority stake|strategic investment|"
    r"\bstakes? in\b",
    re.IGNORECASE,
)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6,
          "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "trillion": 1e12}
_DOLLARS = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion|mm|mn|bn|k|m|b|t)?\b", re.IGNORECASE)
_UNITS = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|m)?\s+(?:new |common )*(units|shares)\b", re.IGNORECASE)


def largest_amount(text: str, price: Optional[float] = None) -> Optional[float]:
    """The biggest dollar figure in the text; share or unit counts are valued at `price`."""
    found = []
    for m in _DOLLARS.finditer(text):
        if re.match(r"\s*(per|a|/)\s*(share|unit)", text[m.end():], re.IGNORECASE):
            continue  # a price, not the size of the deal
        if re.match(r"\s*(in|of)\s+(total\s+)?(assets|deposits|loans|revenue|sales|annual)", text[m.end():], re.IGNORECASE):
            continue  # the target's balance sheet or revenue, not the price paid
        num, unit = m.groups()
        found.append(float(num.replace(",", "")) * _SCALE.get((unit or "").lower(), 1))
    if price:
        for num, unit, _ in _UNITS.findall(text):
            found.append(float(num.replace(",", "")) * _SCALE.get(unit.lower(), 1) * price)
    return max(found) if found else None


def weight(text: str, market_cap: Optional[float] = None, price: Optional[float] = None) -> int:
    """3 = can reprice the company (deal, trial readout, guidance change, CEO), 2 = notable, 1 = minor.

    A deal, contract or settlement is also weighed against the company: under 3% of its market
    value counts 1, under 10% counts 2, whatever the verb. A minority stake in someone else, a deal
    whose terms were not disclosed, a subsidiary's deal, or an unconfirmed rumor counts 1, and a deal
    with no price in the headline counts at most 2.
    """
    w = 3 if _TIER3.search(text) else 2 if _TIER2.search(text) else 1
    if (re.search(r"\b(unit|subsidiary|division)\b", text, re.IGNORECASE)
            and re.search(r"takeover|sale|sell|divest|acqui|deal|spin", text, re.IGNORECASE)
            and largest_amount(text, price) is None):
        w = min(w, 2)  # a deal for a unit, with no price: the parent's exposure is unknown and usually small
    if (re.search(r"\b(completes?|completed|closes?|closed|completion of|finali[sz]es?)\b.{0,40}\b(acquisition|merger|sale|divestiture|deal)\b",
                  text, re.IGNORECASE)
            or re.search(r"\bon \w+ \d{1,2}, \d{4},? .{0,80}\b(acquired|completed the acquisition|closed the acquisition)\b", text, re.IGNORECASE)):
        return 1  # a closing: the deal was priced when it was announced
    if re.search(r"\bpublished (in|by) (the )?(new england|nejm|lancet|nature|jama|science|journal)", text, re.IGNORECASE):
        return 1  # the paper of a trial whose result was announced when it read out
    if re.search(r"\bunconfirmed\b|\brumou?rs?\b|\bspeculation\b", text, re.IGNORECASE):
        return 1  # still a case SCOUT can flag, but it ranks below anything confirmed
    if re.search(r"^\s*['\"\u2018\u201c].+['\"\u2019\u201d]\s*-\s*[A-Z][\w .&]{1,40}$", text.split("\n")[0]):
        w = min(w, 2)  # "'...' - Bloomberg News": a relayed media report, not the company speaking
    financing = re.search(r"\b(debt|loan|financing|credit facility|notes offering)\b", text, re.IGNORECASE)
    if not financing and (_UNSIZED.search(text) or not _SIZED.search(text)):
        return w  # a lender's or underwriter's role in someone else's deal is weighed by its size below
    if (_MINORITY.search(text) or re.search(r"terms (were )?not disclosed|undisclosed", text, re.IGNORECASE)
            or re.search(r", an? [^,]{2,40}\b(company|subsidiary|unit)\b,|\bsubsidiary\b", text, re.IGNORECASE)):
        return 1  # a slice of someone else, or a deal too small for the buyer to have to price it
    amount = largest_amount(text, price)
    if amount is None:
        return min(w, 2)  # a buyer-side deal with no price is rarely big: material ones are priced
    if market_cap:
        share = amount / market_cap
        w = min(w, 1 if share < 0.03 else 2 if share < 0.10 else 3)
    return w


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

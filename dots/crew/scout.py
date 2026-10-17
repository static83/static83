"""SCOUT's data side: pull the newest 8-K filings from SEC EDGAR and apply the hard filters.

The model never sees a paraphrase. It sees the filing text itself, trimmed to a fixed length.
"""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests

from .config import EDGAR_ITEMS, settings
from .schema import Candidate

EDGAR_CURRENT = "https://www.sec.gov/cgi-bin/browse-edgar"
EDGAR_TICKERS = "https://www.sec.gov/files/company_tickers.json"
EDGAR_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:0>10}.json"
# Tried in order. Companies with several share classes often tag only the class-level dei figure,
# which the API leaves out, so fall back to the statement-level counts.
SHARE_CONCEPTS = [
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
    ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"),
    ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),
]
ATOM = {"a": "http://www.w3.org/2005/Atom"}
EXCERPT_CHARS = 6000
_ITEM_RE = re.compile(r"Item\s+(\d\.\d\d)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class Edgar:
    """Thin, polite EDGAR client. SEC asks for a descriptive User-Agent and under 10 req/s."""

    def __init__(self, user_agent: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent or settings.edgar_user_agent
        self.session.headers["Accept-Encoding"] = "gzip, deflate"
        self._tickers: Optional[dict[str, dict]] = None

    def _get(self, url: str, **kw) -> requests.Response:
        resp = self.session.get(url, timeout=30, **kw)
        resp.raise_for_status()
        time.sleep(0.15)
        return resp

    # ---- feed -------------------------------------------------------------------------------
    def latest_8k(self, count: int = 100) -> list[dict]:
        """Newest 8-K entries from the 'current filings' Atom feed."""
        resp = self._get(
            EDGAR_CURRENT,
            params={"action": "getcurrent", "type": "8-K", "count": count, "output": "atom"},
        )
        root = ET.fromstring(resp.content)
        out = []
        for entry in root.findall("a:entry", ATOM):
            title = entry.findtext("a:title", default="", namespaces=ATOM)
            link = entry.find("a:link", ATOM)
            updated = entry.findtext("a:updated", default="", namespaces=ATOM)
            summary = html.unescape(entry.findtext("a:summary", default="", namespaces=ATOM))
            m = re.match(r"8-K(?:/A)?\s+-\s+(.*?)\s+\((\d{6,10})\)", title)
            if not m or link is None:
                continue
            acc = re.search(r"AccNo:</b>\s*([\d-]+)", summary)
            out.append(
                {
                    "company": m.group(1).strip(),
                    "cik": m.group(2).lstrip("0"),
                    "index_url": link.get("href", ""),
                    "filed_at": updated,
                    "accession": acc.group(1) if acc else "",
                    "amendment": title.startswith("8-K/A"),
                }
            )
        return out

    # ---- per filing -------------------------------------------------------------------------
    def primary_document(self, index_url: str) -> tuple[str, str]:
        """Return (url, plain text) of the filing's main 8-K document, not an exhibit."""
        page = self._get(index_url).text
        chosen = None
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
            href = re.search(r'href="(?:/ix\?doc=)?(/Archives/[^"]+\.htm[l]?)"', row)
            cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)]
            if href and any(c.upper() in ("8-K", "8-K/A") for c in cells):
                chosen = href.group(1)
                break
        if chosen is None:  # fall back to the first non-index document
            hrefs = re.findall(r'href="(/Archives/[^"]+\.htm[l]?)"', page)
            chosen = next((h for h in hrefs if "-index" not in h and not h.endswith("index.htm")), None)
        if chosen is None:
            return index_url, _clean(page)
        url = "https://www.sec.gov" + chosen
        return url, _clean(self._get(url).text)

    def tickers(self) -> dict[str, dict]:
        if self._tickers is None:
            data = self._get(EDGAR_TICKERS).json()
            self._tickers = {str(v["cik_str"]): v for v in data.values()}
        return self._tickers

    def shares_outstanding(self, cik: str, max_age_days: int = 400) -> Optional[float]:
        """Most recent share count the SEC has for this company, or None if nothing recent exists."""
        try:
            facts = self._get(EDGAR_FACTS.format(cik=cik)).json().get("facts", {})
        except (requests.RequestException, ValueError):
            return None
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).date().isoformat()
        for group, concept in SHARE_CONCEPTS:
            rows = facts.get(group, {}).get(concept, {}).get("units", {}).get("shares", [])
            rows = [r for r in rows if r.get("val") and r.get("end", "") >= cutoff]
            if not rows:
                continue  # missing or stale: try the next concept
            latest = max(r["end"] for r in rows)
            on_latest = [r for r in rows if r["end"] == latest]
            if group == "dei":
                # the cover-page figure is reported once per share class; add the classes up,
                # taking the most recent filing for each class value
                by_filing = max(r.get("filed", "") for r in on_latest)
                return float(sum(r["val"] for r in on_latest if r.get("filed", "") == by_filing))
            return float(max(on_latest, key=lambda r: r.get("filed", ""))["val"])
        return None


# Filings that only settle news already made public. Checked against the start of the filing body.
_COMPLETES_PRIOR = re.compile(
    r"\b(completed|closed|consummated|settled|priced)\b[^.]{0,80}?\bpreviously (announced|disclosed|reported)\b"
    r"|\b(completion|closing|consummation|settlement|pricing) of (its|the|our) previously (announced|disclosed)\b",
    re.IGNORECASE,
)
_CONVERT_CLOSING = [re.compile(p, re.IGNORECASE) for p in (r"\bindenture\b", r"\bconvertible senior notes\b", r"\binitial purchasers?\b")]
_UNDERWRITTEN = re.compile(r"\bentered into an underwriting agreement\b", re.IGNORECASE)
# Offering paperwork is only skipped when the filing reports nothing else.
_FINANCING_ONLY_ITEMS = {"1.01", "7.01", "8.01"}


def settles_prior_news(items: list[str], body: str) -> Optional[str]:
    """Why this filing only settles something announced earlier, or None if it may be new."""
    head = body[:3000]
    m = _COMPLETES_PRIOR.search(head)
    if m:
        return f"settles a previously announced deal ('{m.group(0)[:70]}')"
    if set(items) <= _FINANCING_ONLY_ITEMS:
        if all(p.search(head) for p in _CONVERT_CLOSING):
            return "closing of a convertible bond offering, which is announced when it is priced"
        if _UNDERWRITTEN.search(head):
            return "underwritten offering, which is announced the day it is priced"
    return None


def _clean(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def items_in(text: str) -> list[str]:
    seen: list[str] = []
    for code in _ITEM_RE.findall(text):
        if code in EDGAR_ITEMS and code not in seen:
            seen.append(code)
    return seen


def excerpt(text: str) -> str:
    """The part of the filing after the cover page, fixed length, untouched otherwise."""
    start = text.find("Item ")
    body = text[start:] if start >= 0 else text
    return body[:EXCERPT_CHARS]


def find_candidates(
    edgar: Edgar,
    price_of,
    limit: Optional[int] = None,
    since_hours: float = 18,
    log=print,
    skip: set[str] | None = None,
) -> list[Candidate]:
    """Apply the SCOUT filters in order and hand back at most `limit` candidates.

    `price_of(ticker) -> float | None` is injected so SCOUT does not import the broker.
    """
    limit = limit or settings.max_candidates
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    tickers = edgar.tickers()
    picked: list[Candidate] = []
    for entry in edgar.latest_8k():
        if len(picked) >= limit:
            break
        if entry["amendment"]:
            continue
        try:
            filed = datetime.fromisoformat(entry["filed_at"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if filed < cutoff:
            continue
        info = tickers.get(entry["cik"])
        if not info:  # no listed ticker, nothing to paper trade
            continue
        ticker = info["ticker"]
        if skip and (entry["accession"] in skip or f"{ticker}|{entry['filed_at']}" in skip):
            log(f"  skip {ticker}: already judged on an earlier night")
            continue
        url, text = edgar.primary_document(entry["index_url"])
        items = items_in(text)
        if not items:
            log(f"  skip {ticker}: routine items only")
            continue
        prior = settles_prior_news(items, excerpt(text))
        if prior:
            log(f"  skip {ticker}: {prior}")
            continue
        price = price_of(ticker)
        shares = edgar.shares_outstanding(entry["cik"])
        if not price:
            log(f"  skip {ticker}: no price at Alpaca, so it cannot be paper traded")
            continue
        if not shares:
            log(f"  skip {ticker}: no share count on file at the SEC, market cap unknown")
            continue
        cap = price * shares
        if cap < settings.min_market_cap:
            log(f"  skip {ticker}: market cap {cap/1e6:,.0f}M below floor")
            continue
        picked.append(
            Candidate(
                ticker=ticker,
                company=entry["company"],
                cik=entry["cik"],
                accession=entry["accession"],
                items=items,
                filed_at=entry["filed_at"],
                url=url,
                source_excerpt=excerpt(text),
                market_cap_usd=cap,
                last_price=price,
            )
        )
        log(f"  pulled {ticker}: items {', '.join(items)}")
    return picked

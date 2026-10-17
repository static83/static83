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
EDGAR_SHARES = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:0>10}/dei/EntityCommonStockSharesOutstanding.json"
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

    def shares_outstanding(self, cik: str) -> Optional[float]:
        try:
            data = self._get(EDGAR_SHARES.format(cik=cik)).json()
        except requests.RequestException:
            return None
        units = data.get("units", {}).get("shares", [])
        if not units:
            return None
        latest = max(units, key=lambda u: u.get("end", ""))
        return float(latest.get("val") or 0) or None


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
        url, text = edgar.primary_document(entry["index_url"])
        items = items_in(text)
        if not items:
            log(f"  skip {ticker}: routine items only")
            continue
        price = price_of(ticker)
        shares = edgar.shares_outstanding(entry["cik"])
        cap = (price or 0) * (shares or 0) or None
        if cap is None:
            log(f"  skip {ticker}: market cap unknown (no quote or no common shares on file)")
            continue
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

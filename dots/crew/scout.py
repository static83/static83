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
    def latest_8k(self, count: int = 100, since: Optional[datetime] = None, max_pages: int = 10) -> list[dict]:
        """8-K entries from the 'current filings' Atom feed, newest first.

        The feed serves 100 entries a page, which is only an hour or two on a busy evening, so keep
        paging until entries are older than `since` (or max_pages is reached).
        """
        out: list[dict] = []
        for page in range(max_pages):
            resp = self._get(
                EDGAR_CURRENT,
                params={"action": "getcurrent", "type": "8-K", "count": count, "start": page * count, "output": "atom"},
            )
            root = ET.fromstring(resp.content)
            entries = root.findall("a:entry", ATOM)
            for entry in entries:
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
            if len(entries) < count or since is None or not out:
                break
            try:
                oldest = datetime.fromisoformat(out[-1]["filed_at"].replace("Z", "+00:00"))
            except ValueError:
                break
            if oldest < since:
                break
        seen, unique = set(), []
        for r in out:  # the feed can repeat an entry across page boundaries
            if r["accession"] not in seen:
                seen.add(r["accession"])
                unique.append(r)
        return unique

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
            # One company can have several tickers (common, preferreds, notes). The SEC lists the
            # primary common stock first, so keep the first entry for each company.
            self._tickers = {}
            for _, v in sorted(data.items(), key=lambda kv: int(kv[0])):
                self._tickers.setdefault(str(v["cik_str"]), v)
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
_NOTES_CLOSING = [re.compile(p, re.IGNORECASE) for p in (
    r"\bindenture\b",
    r"\b(senior |convertible |subordinated )*notes due \d{4}\b",
    r"\b(initial purchasers?|underwriters?|book-running managers?)\b",
)]
_CONVERTIBLE = re.compile(r"\bconvertible\b", re.IGNORECASE)
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
        if all(p.search(head) for p in _NOTES_CLOSING):
            kind = "convertible bond" if _CONVERTIBLE.search(head) else "bond"
            return f"closing of a {kind} offering, which is announced when it is priced"
        if _UNDERWRITTEN.search(head):
            return "underwritten offering, which is announced the day it is priced"
    return None


# Item 5.02 on its own is usually board seats and pay. It is news when a top officer leaves or
# arrives, or when someone leaves over a disagreement. When unsure, the filing goes to SCOUT.
_KEY_OFFICER = re.compile(
    r"chief executive officer|chief financial officer|chief operating officer|principal (executive|financial) officer"
    r"|\bCEO\b|\bCFO\b|\bCOO\b|(?<!vice )(?<!vice-)\bpresident\b",
    re.IGNORECASE,
)
_LEAVES = re.compile(
    r"\b(resign\w*|terminat\w*|depart\w*|step(s|ped|ping)? down|separat\w*|retire\w*|remov\w*|dismiss\w*"
    r"|ceased?|will leave|left the company|transition\w* out)\b",
    re.IGNORECASE,
)
_ARRIVES = re.compile(r"\b(appoint\w*|named|promot\w*|succeed\w*|hired|elected)\b", re.IGNORECASE)
_PAY_WORDS = re.compile(r"\b(compensation|bonus\w*|award\w*|vest\w*|salary|severance|incentive|equity grant|plan)\b", re.IGNORECASE)
_DISAGREEMENT = re.compile(r"disagreement", re.IGNORECASE)
_NEGATED = re.compile(r"\b(not|no|without|neither|nor|never)\b.{0,80}disagreement", re.IGNORECASE)
_ABBREV_END = re.compile(
    r"(\b(Inc|Corp|Co|Ltd|LLC|Mr|Ms|Mrs|Dr|No|Jr|Sr|St|Nos|Messrs|plc)\.|\b[A-Z]\.|\b(L\.L\.C|N\.V|S\.A|U\.S|M\.D|Ph\.D|M\.B\.B\.S|B\.V)\.)$"
)


def _sentences(text: str) -> list[str]:
    """Split on sentence ends, but not after 'Inc.', 'Mr.', 'M.D.' or a middle initial."""
    out: list[str] = []
    for piece in re.split(r"(?<=[.;])\s+", text):
        if out and _ABBREV_END.search(out[-1]):
            out[-1] += " " + piece
        else:
            out.append(piece)
    return out


def routine_people_filing(items: list[str], body: str) -> Optional[str]:
    """Why an Item 5.02-only filing is routine, or None if it may be news (or is not 5.02-only)."""
    if items != ["5.02"]:
        return None
    head = body[:5000]
    if not re.search(r"\bitem\s+5\.02", head, re.IGNORECASE):
        return None  # could not find the section; let SCOUT read it
    sentences = _sentences(head)
    for sent in sentences:
        if _DISAGREEMENT.search(sent) and not _NEGATED.search(sent):
            return None  # someone left over a disagreement: always news
        if _KEY_OFFICER.search(sent) and not _PAY_WORDS.search(sent):
            if _LEAVES.search(sent) or _ARRIVES.search(sent):
                return None  # a top officer is leaving or arriving
    if any(_PAY_WORDS.search(x) for x in sentences[:6]):
        return "routine pay or award filing, no top officer leaving or arriving"
    return "routine board or officer change, no top officer leaving or arriving"


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
    m = re.search(r"\bitem\s+\d\.\d\d", text, re.IGNORECASE)
    body = text[m.start():] if m else text
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
    for entry in edgar.latest_8k(since=cutoff):
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
        same = next((c for c in picked if c.ticker == ticker), None)
        if same is not None:
            # one company, one case a night: fold its other filing into the case SCOUT already has
            url2, text2 = edgar.primary_document(entry["index_url"])
            same.source_excerpt += f"\n\n[Second filing the same night, {entry['filed_at']}, {url2}]\n" + excerpt(text2)[:3000]
            same.items = sorted(set(same.items) | set(items_in(text2)))
            log(f"  fold {ticker}: second filing added to tonight's case")
            continue
        if skip and (entry["accession"] in skip or f"{ticker}|{entry['filed_at']}" in skip):
            log(f"  skip {ticker}: already judged on an earlier night")
            continue
        url, text = edgar.primary_document(entry["index_url"])
        items = items_in(text)
        if not items:
            log(f"  skip {ticker}: routine items only")
            continue
        prior = settles_prior_news(items, excerpt(text)) or routine_people_filing(items, excerpt(text))
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

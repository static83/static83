"""SCOUT's data side: pull the newest 8-K filings from SEC EDGAR and apply the hard filters.

The model never sees a paraphrase. It sees the filing text itself, trimmed to a fixed length.
"""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
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
_AS_PREVIOUSLY = re.compile(r"\bas previously (disclosed|announced|reported)\b", re.IGNORECASE)
_COMPLETED_DEAL = re.compile(
    r"\b(completed|consummated|closed)\b.{0,60}?\b(acquisition|merger|sale|disposition|purchase)\b", re.IGNORECASE
)
_UNDERWRITTEN = re.compile(r"\bentered into an underwriting agreement\b", re.IGNORECASE)
# Offering paperwork is only skipped when the filing reports nothing else.
_FINANCING_ONLY_ITEMS = {"1.01", "7.01", "8.01"}


def settles_prior_news(items: list[str], body: str) -> Optional[str]:
    """Why this filing only settles something announced earlier, or None if it may be new."""
    head = body[:3000]
    m = _COMPLETES_PRIOR.search(head)
    if m:
        return f"settles a previously announced deal ('{m.group(0)[:70]}')"
    if "2.01" in items and "2.02" not in items and _AS_PREVIOUSLY.search(head) and _COMPLETED_DEAL.search(head):
        return "completes a previously disclosed acquisition or sale"
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
    if items == ["5.02", "7.01"]:
        # Allowed only when the 7.01 section just furnishes a press release about the same people news.
        m = re.search(r"\bitem\s+7\.01\b(.*?)(\bitem\s+9\.01\b|$)", body[:6000], re.IGNORECASE | re.DOTALL)
        reg_fd = m.group(1) if m else ""
        if not (re.search(r"press release", reg_fd, re.IGNORECASE)
                and re.search(r"appoint|resign|retire|director|board|depart", reg_fd, re.IGNORECASE)
                and not _HAS_FIGURES.search(reg_fd)):
            return None
    elif items != ["5.02"]:
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


# Earnings filed after the market has already traded on them. Only for filings that report
# nothing but results (2.02, optionally with the 7.01 that furnishes the same release).
_EARNINGS_ONLY_ITEMS = {"2.02", "7.01"}
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_RELEASE_DATE = re.compile(
    rf"\b(?:On|(?:press|news|earnings) release dated) ({_MONTHS}) (\d{{1,2}}),? (\d{{4}})", re.IGNORECASE
)
_RELEASE_WORDS = re.compile(r"press release|news release|earnings release|announced|issued|reported|released", re.IGNORECASE)


NY = ZoneInfo("America/New_York")


def _sessions_closed(after: datetime, until: datetime) -> int:
    """Weekday sessions whose 4pm New York close falls after `after` and by `until` (holidays not excluded)."""
    after, until = after.astimezone(NY), until.astimezone(NY)
    n, d = 0, after.date()
    while d <= until.date():
        close = datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY)
        if d.weekday() < 5 and after < close <= until:
            n += 1
        d += timedelta(days=1)
    return n


def late_earnings(items: list[str], body: str, filed_at: str, now: Optional[datetime] = None) -> Optional[str]:
    """Why an earnings-only filing is stale by the time the crew runs, or None if it may still be fresh.

    Stale means at least one full session has closed between the release and now. When the release
    is dated the same day as the filing, the filing time stands in for the release time (companies
    file the 8-K within minutes of the release); an earlier release date is taken as late that day.
    """
    if not items or not set(items) <= _EARNINGS_ONLY_ITEMS or "2.02" not in items:
        return None
    try:
        filed = datetime.fromisoformat(filed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    # Default: the release went out when the 8-K was filed (they are filed within minutes of each
    # other, and a release cannot come after its own filing). An earlier dated release overrides it.
    released, when = filed, f"{filed.astimezone(NY):%b %d %H:%M} ET"
    for sent in _sentences(body[:2500]):
        m = _RELEASE_DATE.search(sent)
        if m and _RELEASE_WORDS.search(sent):
            released_day = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
            if released_day < filed.astimezone(NY).date():
                released = datetime(released_day.year, released_day.month, released_day.day, 23, 59, tzinfo=NY)
                when = f"{released_day:%b %d}"
            break
    sessions = _sessions_closed(released, now)
    if sessions >= 1:
        return f"earnings out {when}; the market has traded {sessions} full session(s) on them"
    return None


# Any filing: did the stock already react after the news went public?
_PUBLIC_WORDS = re.compile(r"press release|news release|earnings release|announced|publicly (disclosed|released)", re.IGNORECASE)
REACTION_MOVE = 0.05   # open or close at least 5% from the prior close
REACTION_VOLUME = 2.0  # on at least twice the median daily volume of the prior 20 sessions


def first_exposed_session(body: str, filed_at: str) -> date:
    """The first trading day whose session could have traded on this news.

    If the filing dates a press release or announcement, that day's session counts (the release may
    have gone out before the open even when the 8-K was filed after the close). Otherwise the news
    was public when the 8-K was filed: that day's session if filed before 4pm New York, else the next.
    """
    filed = datetime.fromisoformat(filed_at.replace("Z", "+00:00")).astimezone(NY)
    for sent in _sentences(body[:2500]):
        m = _RELEASE_DATE.search(sent)
        if m and _PUBLIC_WORDS.search(sent):
            day = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
            if day <= filed.date():
                return day
            break
    return filed.date() if filed.hour < 16 else filed.date() + timedelta(days=1)


# Alpaca's free feed is IEX only, a few percent of all US volume, so the bar is set low:
# $25k a day on IEX is very roughly $1M a day across all exchanges: plenty for a $5,000 position.
MIN_IEX_DOLLAR_VOLUME = 25_000


def too_thin(bars: list[dict]) -> Optional[str]:
    """Why a stock trades too little for the crew to get in and out sensibly, or None."""
    recent = bars[-20:]
    if len(recent) < 10:
        return "fewer than 10 sessions of price history"
    dollars = sorted(float(b["v"]) * float(b["c"]) for b in recent)
    median = dollars[len(dollars) // 2]
    if median < MIN_IEX_DOLLAR_VOLUME:
        return f"too thinly traded: about ${median:,.0f} a day on the IEX feed"
    return None


def already_traded(bars: list[dict], first_day: date, now: Optional[datetime] = None, look: int = 3) -> Optional[str]:
    """Why the market has visibly traded on this news already, or None."""
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    idx = [i for i, b in enumerate(bars) if b["t"][:10] >= first_day.isoformat()]
    if not idx or idx[0] == 0:
        return None
    start = idx[0]
    base = sorted(float(b["v"]) for b in bars[max(0, start - 20):start])
    median_vol = base[len(base) // 2] if base else 0
    for i in idx[:look]:
        b, prev = bars[i], bars[i - 1]
        day = date.fromisoformat(b["t"][:10])
        if datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY) > now:
            break  # that session has not closed yet
        prior = float(prev["c"])
        gap, move = float(b["o"]) / prior - 1, float(b["c"]) / prior - 1
        vol = float(b["v"]) / median_vol if median_vol else 0
        if max(abs(gap), abs(move)) >= REACTION_MOVE and vol >= REACTION_VOLUME:
            return (f"already traded: on {day:%b %d} it opened {gap:+.0%} and closed {move:+.0%} "
                    f"on {vol:.1f}x normal volume")
    return None


# A notice that results or a call are coming, with no results in it.
_NOTICE_ONLY_ITEMS = {"7.01", "8.01"}
_CALL_NOTICE = re.compile(
    r"will (host|hold) (a|its|an) (conference call|earnings call|webcast|call)"
    r"|(annual|special) meeting of (stockholders|shareholders|unitholders) will be held"
    r"|(conference call|earnings call|webcast)[^.]{0,120}\bto (discuss|review|announce)\b[^.]{0,60}\bresults"
    r"|will (release|report|announce) (its )?[^.]{0,60}\bresults\b[^.]{0,80}\b(on|before|after) "
    r"(january|february|march|april|may|june|july|august|september|october|november|december|monday|tuesday|wednesday|thursday|friday)",
    re.IGNORECASE,
)
_HAS_FIGURES = re.compile(
    r"revenues? (of|was|were|increased|decreased)|net (income|loss) (of|was)|earnings per share|per diluted share"
    r"|preliminary|guidance|outlook|\$\s?\d[\d,.]*\s?(million|billion)",
    re.IGNORECASE,
)


def call_notice_only(items: list[str], body: str) -> Optional[str]:
    """Why a filing is only a date for an upcoming call or results, or None."""
    if not items or not set(items) <= _NOTICE_ONLY_ITEMS:
        return None
    head = " ".join(_sentences(body[:2500])[:3])
    if _CALL_NOTICE.search(head) and not _HAS_FIGURES.search(body[:2500]):
        return "only a date for an upcoming call, results or shareholder meeting, no figures"
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
    m = re.search(r"\bitem\s+\d\.\d\d", text, re.IGNORECASE)
    body = text[m.start():] if m else text
    return body[:EXCERPT_CHARS]


# Items that on their own can reprice a company, and items that usually matter.
# 2.01 (deal completed) is only notable: the deal was nearly always announced earlier.
_HEAVY_ITEMS = {"1.03", "2.02", "3.01", "4.02"}
_NOTABLE_ITEMS = {"1.01", "1.02", "2.01", "2.05", "2.06", "4.01", "5.01", "5.02"}


def filing_weight(items: list[str], body: str) -> int:
    """1-3, like news.weight. 'Other events' filings are weighed by what their text announces."""
    from .news import weight

    w = 3 if _HEAVY_ITEMS & set(items) else 2 if _NOTABLE_ITEMS & set(items) else 1
    if w < 3 and {"7.01", "8.01"} & set(items):
        said = re.split(r"\bitem\s+9\.01|\bsignatures?\b", body, maxsplit=1, flags=re.IGNORECASE)[0]
        w = max(w, weight(said[:800]))  # stop before the exhibit list and the signer's title
    return w


def find_candidates(
    edgar: Edgar,
    price_of,
    bars_of=None,
    limit: Optional[int] = None,
    since_hours: float = 18,
    log=print,
    skip: set[str] | None = None,
    recent_tickers: Iterable[str] = (),
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
            same.also_accessions.append(entry["accession"])
            log(f"  fold {ticker}: second filing added to tonight's case")
            continue
        if skip and (entry["accession"] in skip or f"{ticker}|{entry['filed_at']}" in skip):
            log(f"  skip {ticker}: already judged on an earlier night")
            continue
        if ticker in recent_tickers:
            log(f"  skip {ticker}: the crew judged this company in the last 3 days")
            continue
        url, text = edgar.primary_document(entry["index_url"])
        items = items_in(text)
        if not items:
            log(f"  skip {ticker}: routine items only")
            continue
        body = excerpt(text)
        prior = (settles_prior_news(items, body) or routine_people_filing(items, body)
                 or late_earnings(items, body, entry["filed_at"]) or call_notice_only(items, body))
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
        if cap >= settings.min_market_cap and bars_of is not None:
            bars = bars_of(ticker, 40)
            thin = too_thin(bars)
            if thin:
                log(f"  skip {ticker}: {thin}")
                continue
            traded = already_traded(bars, first_exposed_session(body, entry["filed_at"]))
            if traded:
                log(f"  skip {ticker}: {traded}")
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
                weight=filing_weight(items, excerpt(text)),
            )
        )
        log(f"  pulled {ticker}: items {', '.join(items)}")
    return picked


# ---- second source: Benzinga news through Alpaca ------------------------------------------
def find_news_candidates(
    edgar: Edgar,
    data,
    since_hours: float,
    log=print,
    skip: set[str] | None = None,
    recent_tickers: set[str] | None = None,
    now: Optional[datetime] = None,
) -> list[Candidate]:
    """Material single-company news no regular session has traded yet, through the same checks
    as filings: a price at Alpaca, a recent share count, the market-cap floor, and no visible
    reaction already."""
    from .news import classify, clean_text, fetch_news, first_public_session, weight

    now = now or datetime.now(timezone.utc)
    by_ticker = {v["ticker"]: (cik, v) for cik, v in edgar.tickers().items()}
    out: list[Candidate] = []
    seen_tickers: set[str] = set()
    stale = 0

    def subject(item: dict) -> Optional[str]:
        """The company the headline is about: of the tagged symbols, the one named first in it."""
        tagged = [t for t in item["symbols"] if t in by_ticker]
        head = item["headline"].lower()
        best, best_pos = None, len(head) + 1
        for t in tagged:
            name = re.sub(r"[^a-z0-9 ]", " ", by_ticker[t][1].get("title", "").lower()).split()
            spots = [m.start() for m in re.finditer(rf"\b{re.escape(t.lower())}\b", head)]
            if name and name[0] in head:
                spots.append(head.index(name[0]))
            if spots and min(spots) < best_pos:
                best, best_pos = t, min(spots)
        return best or (tagged[0] if tagged else None)

    for item in fetch_news(data, now - timedelta(hours=since_hours), now):
        if classify(item):
            continue
        ticker = subject(item)
        if ticker is None:
            continue  # not a US-listed SEC registrant, nothing to paper trade
        if ticker in seen_tickers:
            continue  # newest material item per company only
        seen_tickers.add(ticker)
        head = item["headline"]
        created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        if skip and f"news:{item['id']}" in skip:
            log(f"  news skip {ticker}: already judged on an earlier run")
            continue
        if recent_tickers and ticker in recent_tickers:
            log(f"  news skip {ticker}: the crew judged this company in the last 3 days")
            continue
        if _sessions_closed(created, now) >= 1:
            stale += 1  # already traded by a full session; counted, not logged one by one
            continue
        price = data.last_price(ticker)
        if not price:
            log(f"  news skip {ticker}: no price at Alpaca")
            continue
        cik, info = by_ticker[ticker]
        shares = edgar.shares_outstanding(cik)
        if not shares:
            log(f"  news skip {ticker}: no share count on file at the SEC")
            continue
        cap = price * shares
        if cap < settings.min_market_cap:
            log(f"  news skip {ticker}: market cap {cap/1e6:,.0f}M below floor")
            continue
        bars = data.daily_bars(ticker, 40)
        thin = too_thin(bars)
        if thin:
            log(f"  news skip {ticker}: {thin}")
            continue
        traded = already_traded(bars, first_public_session(created), now)
        if traded:
            log(f"  news skip {ticker}: {traded}")
            continue
        body = clean_text(item.get("content")) or clean_text(item.get("summary"))
        excerpt_text = (f"[Benzinga news, {item['created_at']}, by {item.get('author') or 'staff'}] {head}\n"
                        f"{clean_text(item.get('summary'))}\n{body}")[:EXCERPT_CHARS]
        out.append(Candidate(
            ticker=ticker, company=info.get("title", ticker), cik=cik, accession=f"news:{item['id']}",
            items=["news"], filed_at=item["created_at"], url=item.get("url") or "",
            source_excerpt=excerpt_text, market_cap_usd=cap, last_price=price, weight=weight(head),
        ))
        log(f"  news pulled {ticker}: {head[:90]}")
    if stale:
        log(f"  news skip {stale} material item(s) a full session has already traded")
    return out

# DOTS

Six research roles, one router, one rule: **if all six agree right away, start over.**

SCOUT watches the SEC EDGAR 8-K feed. ANALYST builds a thesis. SKEPTIC assumes it is obvious,
late and priced in. QUANT does the arithmetic. RISK holds the only veto. JOURNAL writes the record
with every objection still in it. The Spider is plain code that routes the case and enforces the
handoff rules in [`crew/roles/spider.md`](crew/roles/spider.md).

Execution is **paper only**, through Alpaca's paper endpoint. The broker refuses any other URL.

## Run a night

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add ANTHROPIC_API_KEY and, optionally, Alpaca PAPER keys

python run_night.py --offline      # no keys needed: scripted memos, exercises the whole pipeline
python run_night.py --dry-run      # live EDGAR + Claude, orders are logged but never sent
python run_night.py                # live EDGAR + Claude, paper orders if Alpaca keys are set
```

Each run writes `runs/<stamp>.json` (the ledger), `runs/<stamp>-memos.json` (every memo in full)
and `dashboard/runs/latest.json` (what the dashboard replays).

Two runs a weekday, New York time:

- **08:45, before the open**: news filed since last night. Each case carries the stock's pre-market
  move, so the crew can see what is already being priced; any order waits for the 9:30 open.
- **22:15, after the close**: the day's after-close news, with the after-hours move.

Orders are never placed outside regular hours: Alpaca only takes plain limit orders there, with no
stop-loss or target attached. With cron:

```
45 8  * * 1-5  cd /path/to/dots && .venv/bin/python run_night.py --since-hours 12 >> runs/cron.log 2>&1
15 22 * * 1-5  cd /path/to/dots && .venv/bin/python run_night.py --since-hours 24 >> runs/cron.log 2>&1
```
(cron uses the machine's clock; set `CRON_TZ=America/New_York` where supported.)

## What SCOUT watches, and why

US-listed companies' 8-K filings from EDGAR's current-filings feed. Each item is the original
source with an official timestamp, so "pull the original source" is literal. Filters, in order:

1. Only items 1.01, 2.01, 2.02, 5.02, 7.01, 8.01. Routine items are skipped.
2. A listed ticker (EDGAR's own CIK to ticker map).
3. Not just the paperwork for news that is already public: a filing that says it completed, closed,
   settled or priced something "previously announced", a completed acquisition or sale the filing
   says was previously disclosed, the closing of a bond or convertible bond offering,
   or an underwriting agreement (signed the day an offering is priced). The last two only apply when
   the filing reports nothing else. "As previously disclosed" alone is not enough: filings often use
   it to introduce something genuinely new.
4. Not routine people paperwork. A filing whose only material item is 5.02 is skipped unless a CEO,
   CFO, COO or president is leaving or arriving, or someone left over a disagreement. Board seats,
   pay plans, bonuses and temporary leave are skipped. A 5.02 paired with a 7.01 that only furnishes
   the press release about it counts the same. When the filter cannot tell, SCOUT reads it.
5. Not stale earnings. A filing that only reports results (2.02, with or without 7.01) is skipped
   once a full trading session has closed since the release: results released before the open or
   during the day are skipped by the nightly run, results released after the close pass. The
   release time is the filing time unless the filing dates the release to an earlier day.
6. Not just a call date. A filing whose only items are 7.01 or 8.01 and that only announces when
   a conference call, results or a shareholder meeting will happen, with no figures in it, is skipped.
7. A price at Alpaca, so the stock can actually be paper traded.
8. Market cap at or above `DOTS_MIN_MARKET_CAP_USD` (default 300M): last price times the most recent
   share count the SEC has (cover-page count summed across share classes, then the balance-sheet
   count, then the weighted average from the income statement; anything older than about 13 months
   is ignored). No recent share count, as with a fresh IPO, means a skip.
9. Not already traded. Once the news is public (from the day a dated press release or announcement
   went out, or else from the filing time), if any of the next three closed sessions opened or
   closed at least 5% from the prior close on at least twice the median volume, the market has
   already acted on it. Quiet news that drew no reaction still goes to the crew.
10. Not already judged on an earlier night. A second filing from the same company the same night is
   folded into that company's case.
11. At most `DOTS_MAX_CANDIDATES` handoffs a night (default 3). Depth, not volume.

SCOUT the model then quotes the sentences that matter and flags whether a press release beat the
filing, which is SKEPTIC's "late" objection raised early.

## Second source: Benzinga news

Plenty of news never comes with an 8-K: FDA decisions, contract wins, trading halts, guidance
updates in a press release. SCOUT also reads Benzinga's feed through Alpaca's market-data API
(`/v1beta1/news`, included with the Alpaca keys). Most of it is filler, so an item becomes a case
only if:

1. Its headline names a material event (a deal, FDA or trial result, guidance change, halt,
   lawsuit, bankruptcy, top-officer change, buyback...) and is not commentary, a price-target note,
   a conference-presentation notice, a dosing milestone or a round-up of several companies.
2. It tags one or two companies, and the company it is about (the one named first in the
   headline) is an SEC registrant with a price at Alpaca, a recent share count and a market cap
   above the floor.
3. No regular session has closed since it was published, and the stock has not visibly reacted.
4. The crew has not judged that company in the last three days, and no SEC filing about it is
   already a case tonight. Filings and news are merged newest first, one case per company.

SCOUT treats the story as a pointer: it quotes the primary source (the company's release, the
regulator's notice) and flags unconfirmed reports as unconfirmed. `--no-news` turns the feed off.

## The handoff rules

See [`crew/roles/spider.md`](crew/roles/spider.md). In short: one case at a time, everyone sees
everything, objections are never dropped, send-back is a real move (max 4 rounds), RISK's
objection ends a case, a trade needs numbers not agreement, and the account is paper.

## Layout

```
run_night.py          CLI
crew/
  spider.py           the router; each rule in spider.md is a few lines here
  scout.py            EDGAR feed, filters, source extraction
  llm.py              one Claude call per role, Memo schema enforced by the API
  market.py           Alpaca quotes, bars, gap stats, fee model
  broker.py           Alpaca paper bracket orders; dry-run by default
  ledger.py           events + balance -> runs/*.json and dashboard/runs/latest.json
  offline.py          scripted memos for --offline
  roles/*.md          the six role prompts and the Spider rules
dashboard/index.html  replay UI; loads runs/latest.json, else the built-in sample night
```

## Dashboard

Open `dashboard/index.html` through any static server (`python -m http.server -d dashboard`) and
press REPLAY. With no `runs/latest.json` present it replays a built-in sample night, labelled as
fictional. After a real run, it replays that run's ledger instead.

## Model

`DOTS_MODEL` defaults to `claude-opus-5-5`. Each role is a single structured-output call with the
role prompt as the system prompt and the full case packet as the user message. Refusal fallbacks
are enabled server-side; remove the two `betas`/`fallbacks` lines in `crew/llm.py` to opt out.

## What this is not

A research pipeline that argues with itself catches more bad ideas. It does not create edge.
The sample night's balance curve is a story, not a result, and the dashboard says so.

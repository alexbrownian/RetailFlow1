# RetailFlow — retail attention → signals → price

RetailFlow measures how much retail attention each stock **ticker** and
**theme** receives across **15 finance subreddits, X (Twitter) and
StockTwits**, detects attention take-offs, scores sentiment, combines the two
into a **conviction score**, and emits **BUY/SELL signals with explicit
reasons** — then overlays everything against **Bloomberg prices** to assess
whether the crowd leads the move.

Everything runs from one command — `python update_data.py` — which fetches
the latest posts, turns them into small **text-free daily aggregates**
(`ABSTRACTED_DATA/` — counts + sentiment, no post text kept), rebuilds the
signals, and snapshots them. The date window at the top of the file selects
**live** (up to today) or a **frozen backtest window**.

## Architecture

```
  15 subreddits ──┐
  X (Twitter) ────┤──► fetch_all.py ──► merge / fold (dedup: first seen wins)
  StockTwits ─────┘                          │
                                             ▼
              EXTERNAL machine          ABSTRACTED_DATA/          INTERNAL machine
              posts.parquet   ──build──►  5 text-free   ──git──►  hydrate + fold
              (raw text, private)         aggregates              (no raw text ever)
                                             │
                                             ▼
                          notebooks 08/09/10: conviction (z-scores)
                          and BUY/SELL signals with reasons
                                             │
             pull_bloomberg_prices.py ───────┤ (either machine, PX_LAST)
                                             ▼
                          notebooks 11–16: price overlays
                          (mentions, velocity, conviction, signals)
```

**Two machines, one repository.**
- The **external machine** holds the raw post store (`posts.parquet`,
  ~10.8M posts, gitignored) and can rebuild every aggregate from raw text.
- The **internal machine** holds only `ABSTRACTED_DATA/` (committed, ~2 MB,
  text-free). Notebooks 01–07 require raw text and never run there; 08–16
  run in full. Bloomberg Terminal access exists on both machines, so the
  price overlays render everywhere.
- Every run ends with a safety check that the committed aggregates carry no
  text-bearing columns.

## The one knob: the window

At the top of `update_data.py`:

```python
START_DATE = "2021-01-01"   # inclusive
END_DATE   = ""             # "" = LIVE (to today);  "2021-11-01" = backtest window
PRICE_TOP_N = 150           # how many top-mentioned tickers the price pull covers
```

`END_DATE = ""` → **live fast path**: fetch a week of the most popular posts
(Reddit top-of-week + newest; X Top + Latest + broad discovery queries),
splice the last ~45 days of the aggregates, rerun the signal notebooks, pull
prices, render the overlays. Minutes.

A date → **backtest**: the full chain (01–10) rebuilds every aggregate for
exactly that window; the overlays then draw against prices for the same
window. Command cheat-sheet: **[RUNBOOK.md](RUNBOOK.md)**.

## Folder layout

```
RetailFlow1/
├── update_data.py            # THE one command (window at the top)
├── pull_bloomberg_prices.py  # internal machine: PX_LAST for the overlays (blpapi)
├── run_overlays.py           # refresh only notebooks 11-16
├── check_live_ingestion.py   # freshness check, layer by layer
├── RUNBOOK.md                # scenario cheat-sheet
├── ABSTRACTED_DATA/          # the ONLY committed data: 5 text-free aggregates
├── api_calls/                # live fetchers (FetchLayer Reddit/X, StockTwits)
├── data/                     # gitignored except reference/ (raw, processed, prices)
├── data_ingestion/           # historical backfill + live merge scripts
├── notebooks/                # 01-10 pipeline, 11-16 price overlays
├── src/                      # shared logic (see below)
└── tests/                    # pytest checks for dataset + pipeline + notebooks
```

Key `src/` modules: `abstracted_data.py` (export/hydrate + text-free
aggregation and merge maths), `extract_tickers.py` + `screen_tickers.py` +
`ticker_universe.py` (ticker extraction with data-driven word-ticker
screening), `themes.py` (39 tradeable themes, each anchored to a liquid
instrument), `sentiment.py` (VADER + finance lexicon), `build_mentions.py`
and `inflection.py` (counting + take-off detection).

## Notebooks

| # | purpose |
|---|---|
| 01 | slice the raw store + data-driven word-ticker screening |
| 02–03 | ticker mentions over time; first derivative + take-off days |
| 04–05 | the same, per theme |
| 06–07 | ticker / theme sentiment (VADER + finance lexicon) |
| 08–09 | ticker / theme conviction (mentions x sentiment, z-scores) |
| 10 | BUY/SELL trading signals: 5-check score, cooldown, reasons attached |
| 11–12 | overlays: ticker mention share and first derivative vs price |
| 13–14 | overlays: theme first derivative and conviction vs anchor ETF |
| 15–16 | overlays: BUY/SELL conviction clusters on price + signal report card |

The overlays auto-pick their tickers/themes from the data, normalise to
share-of-chatter (comparable across the archive and live eras), mark where a
young ETF's price history begins, and merge nearby same-direction signals
into single high-conviction markers.

## Counting rules (the important ones)

- **One signal only: raw `mention_count`** — the number of distinct posts
  mentioning a ticker that day. A post mentioning NVDA five times counts
  once (breadth of attention, not verbosity). There is deliberately no
  score-based weighting: archived scores are final scores, so weighting
  day-t mentions by them leaks future information into backtests
  (design_decisions.xlsx #30). Tests enforce the single-column output.
- **Dedup is a contract**: every ingestion path skips ids that already exist
  ("first seen wins"). Id prefixes (`x_`, `st_`, Reddit base36) make
  cross-source collisions impossible.
- **Word-tickers are demoted, not deleted**: symbols that are everyday words
  (EDGE, LOAN, RENT) only count when written as `$cashtags`, decided by a
  measured caps-ratio on this corpus with a wordfreq fallback
  (`src/screen_tickers.py`).
- **Live vs archive volumes differ hugely** (archive: millions of posts/day;
  live fetch: hundreds). Charts therefore default to share-of-chatter
  normalisation, and z-scores use trailing baselines. The coverage table
  printed by every `update_data.py` run shows exactly what data exists,
  month by month, per source.

## Data sources

- **Reddit**: 15 finance subreddits. History from Pushshift archive dumps
  (2008–2025); live via FetchLayer (newest + top-of-week per subreddit).
- **X (Twitter)**: three HuggingFace datasets for history (2015–2020,
  Nov 2023+; note the 2021–2023 gap); live via FetchLayer — top-of-week +
  latest cashtag searches plus broad discovery queries that catch names not
  on any watchlist (the extractor finds every valid ticker in post text).
- **StockTwits**: public symbol streams, no key. Users label their own posts
  Bullish/Bearish — ground truth for calibrating the sentiment engine.
- **Bloomberg**: PX_LAST daily closes via blpapi (available on both
  machines; the prices file stays local and gitignored for licensing
  reasons).

## Setup

```bash
pip install -r requirements.txt --user
```

Create `.env` in the project root with `FETCHLAYER_KEY=...` (see
`data_ingestion/LIVE_INGESTION.md` for all keys). On the internal machine,
also install blpapi (see requirements.txt comment) and run `git pull`
followed by `python update_data.py`.

## Known limitations

- Live coverage is thinner than the archive; signals in the live era lean on
  the share normalisation and the 28-day z warm-up.
- Sentiment is lexicon-based (VADER + finance slang): robust in aggregate,
  weak on sarcasm. Upgrade path: swap a finance-tuned transformer into
  `src/sentiment.py::score_text()`; everything downstream is unchanged.
  StockTwits' author labels provide the calibration set.
- The ticker universe is today's listing plus a curated delisted supplement
  (`src/ticker_universe.py`) — a full point-in-time universe would remove
  the residual survivorship bias.
- Mention spikes measure attention, not direction; the sentiment gate in
  notebook 10 addresses this, but levels remain noisier than changes.

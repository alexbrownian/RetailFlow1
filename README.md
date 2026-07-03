# GIC Retail Tracking

Track how often stock tickers are mentioned on Reddit over time, and detect the
moment a ticker "takes off" (the first-derivative / inflection point).

The flow is simple and always the same:

```
raw dumps          prep_posts.py           01 load / 02 mentions        03 first derivative
data/raw/*.zst  ->  posts.parquet   ->   daily_ticker_counts.parquet  ->  take-off dates
                 (one-time, deduped)         (notebooks read the
                                              parquet directly)
```

Nothing is hardcoded to a specific stock. You pick subreddits, dates, and which
tickers to look at using parameter cells at the top of each notebook.

## Where this fits — Retail Flow Tracker, Stage 1

This repo is **Stage 1** of the three-stage Retail Flow Tracker (see
`Updated Retail Tracking Proposal`):

- **Stage 1 (this repo) — thematic / ticker mention tracking.** Count how often
  tickers and themes are mentioned on Reddit, detect take-off (inflection)
  points, and back-test whether those mentions *lead* price moves.
- **Stage 2 — sentiment analysis** (bull/bear scoring), only if raw mentions
  prove too noisy.
- **Stage 3 — network analysis** of influential X accounts.

The code here already covers the *mention* side of Stage 1 (clean -> mentions ->
inflection -> themes). To **finish** Stage 1 you still need to wire in **price
data** and measure the **lead/lag correlation**. The exact to-do list is at the
bottom: see **"Next steps to complete Stage 1"**.

## Folder layout

```
RetailFlow1/
├── README.md
├── requirements.txt
├── data/
│   ├── raw/         <-- the 15 downloaded .zst dumps (see data/README.md)
│   ├── processed/   <-- posts.parquet = THE dataset + notebook outputs
│   └── reference/   <-- the Nasdaq ticker list is cached here
├── data_ingestion/
│   ├── README.md            # how to (re-)download dumps + rebuild the dataset
│   ├── finance_subreddits.txt
│   └── scripts/
│       ├── prep_posts.py        # raw dumps -> posts.parquet (filters + dedup)
│       ├── check_notebooks.py   # validate / auto-fix broken .ipynb files
│       ├── peek.py              # print first records of a .zst (debug)
│       ├── zst_to_csv.py        # dump one .zst to CSV for Excel (debug)
│       └── read_zst.py          # shared reader used by the two debug tools
├── notebooks/
│   ├── 01_clean_data.ipynb          # LOAD the dataset (parquet, filtered)
│   ├── 02_mentions_over_time.ipynb  # daily ticker mention counts + graphs
│   ├── 03_first_derivative.ipynb    # take-off detection per ticker
│   ├── 04_theme_mentions.ipynb      # keyword-based theme counts
│   └── 05_theme_first_derivative.ipynb
├── src/
│   ├── clean_data.py        # raw record -> tidy 8-column row (normalise)
│   ├── ticker_universe.py   # downloads the official list of valid US tickers
│   ├── extract_tickers.py   # finds $TICKER / TICKER in text (precise rules)
│   ├── build_mentions.py    # posts -> daily mention counts (per ticker)
│   ├── inflection.py        # first-derivative take-off detector
│   └── themes.py            # keyword themes (semis, crypto, ...)
└── tests/
    └── test_pipeline.py     # pytest checks for dataset + pipeline + notebooks
```

## One-time setup

```bash
cd RetailFlow1
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
jupyter notebook                   # opens the notebooks in your browser
```

## Where do I put my data?

Drop any of these into **`data/raw/`** (a folder can hold many files):

| File type | Example | Notes |
|-----------|---------|-------|
| `.zst`    | `wallstreetbets_submissions.zst` | The Pushshift / Reddit torrent format. Read straight away, no need to unzip. |
| `.ndjson` / `.jsonl` | one JSON post per line | Same content, uncompressed. |
| `.csv` / `.parquet`  | a Kaggle/HuggingFace export | Any flat table of posts. |

Then run `python3 data_ingestion/scripts/prep_posts.py` once — it streams,
cleans, dedupes and merges everything into `data/processed/posts.parquet`.
The notebooks never touch the raw files.

## How to run

**Notebook 01 — load.** The dataset (`data/processed/posts.parquet`) is already
built, so this notebook just loads and slices it — **no `.zst` reading, loads in
seconds**. Edit the PARAMETERS cell:

```python
SUBREDDITS  = []      # e.g. ["wallstreetbets"];  [] = ALL 15 subreddits
START_DATE  = None    # "2021-01-01" inclusive, or None
END_DATE    = None    # "2021-02-01" EXCLUSIVE, or None
SLICE_OUT   = None    # set a path to save the filtered slice for notebooks 02/04
```

(Rebuilding the parquet from the raw `.zst` dumps is a separate, slow, one-time
step: `python3 data_ingestion/scripts/prep_posts.py` — see
`data_ingestion/README.md`.)

Notebooks 02 and 04 read `posts.parquet` directly too, with the same
`SUBREDDITS` / `START_DATE` / `END_DATE` parameters. Tip: the notebooks are
JSON files — if one ever breaks (e.g. hand-edited quotes), run
`python3 data_ingestion/scripts/check_notebooks.py --fix`.

**Notebook 02 — mentions over time.** Builds daily counts and draws **two graphs**.

```python
TICKERS_TO_PLOT = []   # e.g. ["GME","AMC"];  [] = automatically use TOP_N
TOP_N           = 6
CASHTAGS_ONLY   = False # True = only count $TICKER (cleaner, lower recall)
```

Run it → two charts: **raw mentions** (one post = 1) and **upvote-weighted
mentions** (each post weighted by score²). Saves
`data/processed/daily_ticker_counts.parquet` with both columns
(`mention_count`, `weighted_count`). First run downloads the Nasdaq ticker list;
needs internet. _Weighted needs raw posts that carry a `score` field — i.e. the
torrent dumps; the real 2021 counts file already in `data/processed/` has raw
counts only._

**Notebook 03 — first derivative.** Finds take-off days.

```python
VALUE_COLUMN = "mention_count"  # or "weighted_count" to run on upvote-weighted signal
TICKERS = []     # e.g. ["GME"];  [] = use TOP_N most mentioned
TOP_N   = 4
SMOOTH  = 3      # rolling-average window (bigger = calmer, slower to react)
K       = 2.0    # std-devs above normal = a take-off (lower = more sensitive)
```

Run it → for each ticker: a chart of mentions with red inflection markers, plus
the printed take-off dates.

## Optional: themes instead of single tickers

`src/themes.py` groups tickers (e.g. NVDA + AMD + SMH = "semiconductors"). It
turns the daily ticker counts into daily *theme* counts with the same columns,
so notebook 03 works on themes unchanged:

```bash
python3 -m src.themes --in data/processed/daily_ticker_counts.parquet \
                      --out data/processed/daily_theme_counts.parquet
```

Then point notebook 03's `DAILY_COUNTS_PATH` at the theme file.

## Weighting assumptions (`build_mentions.py`)

These are the deliberate design choices baked into the signal. Change them if
the back-test suggests a different calibration:

| Parameter | Current value | Why |
|-----------|--------------|-----|
| **Per-post deduplication** | On | A post mentioning NVDA 5 times counts as **1 mention**, not 5. Prevents a single verbose post from inflating the count. |
| **Upvote weight function** | `score²` (power law) | Squaring the upvote score means a 1,000-upvote post contributes 1,000,000 vs a 10-upvote post's 100. Viral posts dominate; low-effort posts barely register. |
| **Comment count (`num_comments`)** | Not used yet | Available in `posts.parquet` — could be added as a second weighting signal (e.g. `score² + α·comments`) if upvote-only proves insufficient. |

If `score` is missing from the raw data (some CSV exports), `weighted_count` falls
back to 0 for all posts; `mention_count` is always valid.

## Glossary

- **Mention count** — number of distinct posts that mentioned a ticker on a given day (one post = 1, regardless of how many times the ticker appears in it).
- **Upvote-weighted count** — sum of `score²` across all posts mentioning the ticker that day. Amplifies viral posts heavily; see weighting assumptions above.
- **First derivative / velocity** — change in (smoothed) mentions vs. the day
  before. High positive value = attention accelerating.
- **Inflection / take-off day** — a day whose velocity is much higher than that
  ticker's normal day-to-day noise (above `mean + K × std`).
- **Ticker universe** — the official list of real, tradeable US symbols, used so
  ordinary capitalised words (CEO, YOLO) are not mistaken for tickers.


---

## Historical vs. live data — how ingestion evolves

The `.zst` dumps are a **one-time historical backfill** (they end in 2025 and
never change). Keep them in `data/raw/` as the immutable source of truth: if
the cleaning rules ever change (new columns, dedupe, different filters),
rebuild `posts.parquet` from them rather than editing the parquet.

**Live/future data will not arrive as `.zst`.** Per Stage 1 step 7, new posts
come from the Reddit API (PRAW) as JSON. Two sensible patterns for appending:

1. **Append to the one parquet** — fetch new posts, run them through the same
   `normalise()` in `src/clean_data.py`, skip any post `id` already in the
   dataset (the same "first seen wins" dedup rule `prep_posts.py` uses), and
   append as new row groups. Simple, keeps a single dataset.
2. **Partitioned parquet** (better for continuous ingestion) — write
   daily/monthly files like `posts/2026-07.parquet`; pyarrow/pandas read the
   whole folder as one dataset, and nothing ever rewrites the 1.1 GB file.

Either way, everything downstream only cares about the 8-column schema
(`id, date, author, score, subreddit, title, selftext, num_comments`). As long
as live data is normalised to that shape, notebooks 02–05 and the tests work
unchanged.

> **⚠️ Caveat — `score` and `num_comments` are snapshots, not final values.**
> The archive dumps captured posts long after posting, so their scores are
> *mature* (a viral post shows its full 50k upvotes). A live-fetched post
> grabbed minutes after creation has a score near 0 — even if it later goes
> viral. This means **upvote-weighted counts from live data are NOT directly
> comparable to historical weighted counts** until the live pipeline re-fetches
> scores after a maturation window (e.g. re-poll each post 24–48h later).
> Raw `mention_count` doesn't suffer from this — one post = 1 either way —
> which is another reason to validate the raw-count signal first before
> leaning on the weighted one.

---

## Potential errors & how to mitigate them

Known ways this signal can lie, and what to do about each:

| Risk | What goes wrong | Mitigation |
|---|---|---|
| **Bot posting / spam** | Pump groups flood a sub with low-effort posts mentioning a ticker; raw `mention_count` spikes with no real crowd behind it | Per-post dedupe is already on (a post counts once). Add: minimum-score filter (e.g. drop `score < 2`), cap posts-per-author-per-day (a user posting $XYZ 30×/day counts once), and cross-check that a spike appears in **more than one** subreddit before trusting it |
| **Upvote maturity bias** | Archived posts carry final/mature scores; live posts have near-zero scores at fetch time — so `weighted_count` systematically favors old data | Don't just delete the weighting — you'd lose the viral-vs-noise distinction. Better options, in order: (a) validate using **raw `mention_count` only** first (it's immune); (b) for live data, re-poll each post's score after a fixed maturation window (24–48h) so old and new are measured at the same age; (c) if re-polling isn't possible, compare each day's weighted count to its own trailing baseline (z-scores) instead of across eras; (d) soften the `score²` power law to `score` or `log(1+score)` so maturity gaps distort less |
| **Selection bias** | Mega-caps (NVDA, TSLA) are always heavily mentioned, so their "spikes" are less meaningful | The inflection detector already normalizes per ticker (`mean + K×std` of its *own* history). Also prefer *velocity* (change) over *level* (absolute counts) when comparing tickers |
| **False-positive tickers** | Bare words like ALL, NOW, IT are valid symbols → phantom mentions | Stop lists + universe validation are already in `extract_tickers.py`; when precision matters more than volume, run with `CASHTAGS_ONLY = True` ($TICKER only) |
| **Deleted/removed posts** | Archive keeps posts later deleted by mods; live API won't return them → historical counts slightly higher | Small effect; note it when back-testing. Optionally drop posts with `selftext == "[removed]"` for consistency |
| **Direction blindness** | A mention spike says *attention*, not *bullish vs bearish* — GME puts and calls look identical | That's Stage 2 (sentiment). Until then, treat spikes as "look here", not "buy signal" |
| **Duplicate/crosspost inflation** | The same content posted across subs counts once per sub | Acceptable if you *want* breadth-of-attention; to remove it, dedupe on identical `title` within a day |

---

## Next steps to complete Stage 1

Goal of Stage 1 (from the proposal): show whether **Reddit mention spikes lead
ETF price moves**, measure the **time lag**, and pin down the **inflection
point**. The four Bloomberg ETFs in the proposal line up with themes this repo
already knows:

| Proposal ETF | Theme in `src/themes.py` |
|--------------|--------------------------|
| GLD  (gold)            | `gold_metals` |
| MAGS (mega-cap tech)   | `ai_megacap` |
| BTC  (crypto)          | `crypto` |
| SMH  (semiconductors)  | `semiconductors` |

Work through these in order:

- [x] **1. Get the data in.** DONE 2026-07-02: the 15 `.zst` dumps are in
      `data/raw/` and `data/processed/posts.parquet` (7.95M posts, all dates)
      is built. To redo with different filters, edit and run
      `python3 data_ingestion/scripts/prep_posts.py`.

- [ ] **2. Produce the mention signals.** Run `notebooks/02_mentions_over_time.ipynb`
      (daily ticker counts, raw **and** upvote-weighted) and
      `notebooks/04_theme_mentions.ipynb` for the four themes above. This gives you
      the daily series you'll line up against prices.

- [ ] **3. Add price data (the missing input).** Export daily price history from
      **Bloomberg** for GLD, MAGS, BTC and SMH and save them as CSVs in a new
      `data/prices/` folder, one file per ETF, columns `date,close`. This is the
      only Stage-1 input the repo doesn't generate itself.

- [ ] **4. Measure lead/lag (the core validation).** Build a small step that, for
      each theme, lines up daily mentions with that ETF's daily return and
      computes the correlation at several lags (e.g. mentions shifted -10..+10
      days). The lag with the strongest correlation tells you **if** mentions lead
      price and **by how many days**. _(I can build this as `src/price_lag.py` +
      a `notebooks/06_mention_price_lag.ipynb` — just ask.)_

- [ ] **5. Define the inflection rule.** Using `notebooks/03_first_derivative.ipynb`,
      settle on the `K` threshold (std-devs above normal) that best lines up with
      the price moves you found in step 4. Write down which `K` you picked and why.

- [ ] **6. Write up findings.** For each theme: does a mention spike lead the ETF?
      what lag? is raw mention count a strong enough signal, or too noisy (which
      would trigger **Stage 2 — sentiment**)? Note the proposal's three concerns:
      bot-posting noise, short-vs-long direction, and selection bias (e.g. NVDA
      already being over-mentioned).

- [ ] **7. Live-API check (only if step 4 is promising).** Validate on recent data
      via the Reddit API (PRAW) once the paid tier is approved, to confirm the
      back-test holds out-of-sample. See "Historical vs. live data" above for the
      ingestion pattern and the score-maturity caveat.

Steps 1-3 are setup/data; **step 4 is the actual Stage-1 result** that decides
whether you proceed straight to live testing or fall back to Stage 2 sentiment.

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
> *mature* (
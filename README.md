# GIC Retail Tracking

Track how often stock tickers are mentioned on Reddit over time, and detect the
moment a ticker "takes off" (the first-derivative / inflection point).

The flow is simple and always the same:

```
raw dumps         prep_posts.py         01 slice + screen          02 mentions                03 first derivative
data/raw/*.zst -> posts.parquet   ->   posts_slice.parquet  ->  daily_ticker_counts.parquet -> take-off dates
               (one-time, deduped)  (+ticker_classification)   (04 -> daily_theme_counts       (05 = same for
                                                                      for themes)               themes)
```

Nothing is hardcoded to a specific stock. **The notebooks chain: 01 → 02 → 03
and 01 → 04 → 05.** You pick subreddits and dates ONCE, in notebook 01; it
saves the filtered slice (`posts_slice.parquet`) that 02 and 04 read, so the
whole chain shares the same time window automatically.

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
│   └── reference/   <-- Nasdaq ticker list + ticker_classification.csv
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
│   ├── screen_tickers.py    # word-ticker screening (case ratio + wordfreq)
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

**Notebook 01 — slice + screen (the head of the chain).** The dataset
(`data/processed/posts.parquet`) is already built, so this notebook loads and
slices it — **no `.zst` reading, loads in seconds** — then saves two things
the rest of the chain uses:

1. `data/processed/posts_slice.parquet` — the filtered slice that notebooks
   02 and 04 read. This is how the whole chain shares ONE time window.
2. `data/reference/ticker_classification.csv` — the word-ticker screening
   table (last section of the notebook) that notebook 02's extractor uses to
   ignore words like LOAN/EDGE/RENT unless written as `$cashtags`.

Edit the two parameter cells:

```python
START_DATE  = '2021-01-01'  # inclusive, or None          <- the window for the WHOLE chain
END_DATE    = '2022-01-01'  # EXCLUSIVE, or None
SUBREDDITS  = []      # e.g. ["wallstreetbets"];  [] = ALL 15 subreddits
SLICE_OUT   = ...posts_slice.parquet   # saved by default; 02/04 need it
```

(Rebuilding the parquet from the raw `.zst` dumps is a separate, slow, one-time
step: `python3 data_ingestion/scripts/prep_posts.py` — see
`data_ingestion/README.md`.)

Notebooks 02 and 04 have **no time-window cells of their own** — they read the
slice and refuse to run (with a clear error) if it doesn't exist yet. To change
the analysis window, edit notebook 01 and re-run the chain from there. Tip: the
notebooks are JSON files — if one ever breaks (e.g. hand-edited quotes), run
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

## Screening word-tickers (`src/screen_tickers.py`)

Many real tickers are also everyday English words — EDGE, LOAN, RENT, TECH,
EARN, OPEN, REAL, CASH... A bare-caps match counts those words as phantom
mentions, and a hand-maintained stop list never ends (there are hundreds of
word-tickers). So the classification is **data-driven**, with two signals:

**Signal 1 — case ratio, measured on our own corpus (primary).** English
words appear mostly lowercase in real posts ("the edge of"); tickers appear
mostly ALL-CAPS ("bought NVDA calls"). For every 4–5-letter symbol in the
universe we count both forms in a post sample and compute
`caps_share = caps / (caps + lower)`. Measured on this dataset: EDGE 0.02,
LOAN 0.002, NVDA 0.93, TSLA 0.93. The distribution is strongly **bimodal**
(most symbols sit near 0 or near 1, few in between) — that gap is what makes
a threshold reliable.

**Signal 2 — wordfreq, general-English frequency (fallback).** If a symbol
has fewer than 30 sightings in the sample, its ratio is noise, so
`zipf_frequency()` from the `wordfreq` package decides instead: "edge" scores
4.7 (common word), "nvda" 2.1 (not a word).

### Design decisions & assumptions

| Decision | Value | Why |
|---|---|---|
| **Corpus beats wordfreq** | signal 1 checked first | Tailored to how Reddit writes. SNAP looks like a word to wordfreq (zipf 4.2) but measures caps_share ≈ 0.56 in the corpus → correctly kept as a ticker. Same protects AMD (zipf 3.5). |
| **Demote, don't delete** | class `cashtag_only` | Demoted tickers still count when written `$LOAN`, so a real attention spike is never lost — only the prose noise. A stop list would delete the signal forever. |
| **caps_share threshold** | 0.5 | The bimodal distribution has its valley roughly between 0.1 and 0.8; 0.5 sits inside it and errs toward precision. |
| **Minimum sightings** | 30 | Below that, one shouty post can flip the ratio. |
| **zipf threshold** | 3.5 | Word-tickers score 4.4–5.1, real tickers ~2.0–2.6. Borderline cases (AMD 3.5) are usually frequent enough to be decided by signal 1 anyway. |
| **Only 4–5 letter symbols screened** | ~9,200 candidates | Only those can collide with the bare-caps regex; 1–3 letter symbols never bare-match by design. |
| **Sample, not full corpus** | 300k posts | Common words are sighted thousands of times in a sample this size; scanning all 7.9M posts changes ratios marginally but costs 25× the time. Sampling is seeded (`random_state=0`) so the CSV is reproducible. |

### Known limitations / thinking points

- **Caps-typed jargon slips through both signals.** HODL (zipf 1.7 — not a
  word; caps_share 0.62 — typed like a ticker) passes both tests. The manual
  `BARE_PROSE_STOP` list in `extract_tickers.py` remains as a third layer for
  exactly these.
- **Brand-name tickers people type lowercase get demoted.** SOFI (0.34),
  HOOD (0.25), COIN (0.04) — lowercase "sofi" is often genuinely about the
  company, so demotion costs some recall. Deliberate: precision-first until
  Stage 2 sentiment; their `$cashtag` mentions always still count.
- **The corpus catches Reddit-specific usage no dictionary would.** ROPE is
  demoted because on WSB it's dark humour, not the ticker — signal 1 gets
  this right where wordfreq alone is borderline (4.1).
- **Ratios are era-dependent.** The notebook measures them on whatever slice
  is loaded (e.g. 2021), the CLI on an all-history sample. Differences are
  small; measuring on your analysis window is arguably a feature.

### How it plugs in

`notebooks/01_clean_data.ipynb` (section "Screen word-tickers") or
`python -m src.screen_tickers` writes
`data/reference/ticker_classification.csv`. `extract_tickers.py` loads it at
import and skips bare-caps matches for every `cashtag_only` ticker — so
notebook 02 and `build_mentions.py` need no changes. If the CSV is missing,
the extractor behaves exactly as before (empty screened set). The layered
defence in the bare-word pass is: universe validation → `STOP_TICKERS`
(jargon, blocks cashtags too) → `BARE_PROSE_STOP` (manual) → `SCREENED_STOP`
(data-driven) — cashtags are exempt from the last two.

## Glossary

- **Mention count** — number of distinct posts that mentioned a ticker on a given day (one post = 1, regardless of how many times the ticker appears in it).
- **Upvote-weighted count** — sum of `score²` across all posts mentioning the ticker that day. Amplifies viral posts heavily; see weighting assumptions above.
- **First derivative / velocity** — change in (smoothed) mentions vs. the day
  before. High positive value = attention accelerating.
- **Inflection / take-off day** — a day whose velocity is much higher than that
  ticker's normal day-to-day noise (above `mean + K × std`).
- **Ticker universe** — the official list of real, tradeable US symbols, used so
  ordinary capitalised words (CEO, YOLO) are not mistaken for tickers.
- **Word-ticker** — a valid symbol that is also an everyday English word (EDGE,
  LOAN, RENT). Classified `cashtag_only` by `src/screen_tickers.py`: bare-caps
  mentions are ignored, `$cashtag` mentions still count.


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
| **False-positive tickers** | Bare words like ALL, NOW, EDGE, LOAN are valid symbols → phantom mentions | Three layers in `extract_tickers.py`: universe validation, manual stop lists, and data-driven screening (`src/screen_tickers.py` — case ratio + wordfreq, see "Screening word-tickers"). When precision matters more than volume, run with `CASHTAGS_ONLY = True` ($TICKER only) |
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
      the daily series you'll line up against prices. Word-ticker screening
      (`data/reference/ticker_classification.csv`, generated 2026-07-03 — see
      "Screening word-tickers") is picked up automatically; re-run notebook 01's
      screening section if you change its thresholds.

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

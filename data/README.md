# data/ — what's in here and where it came from

```
data/
├── raw/         the 15 original Reddit dumps (.zst), exactly as torrented
├── processed/   posts.parquet — the ONE merged dataset the pipeline runs on
└── reference/   Nasdaq/NYSE ticker lists (used by extract_tickers.py)
```

---

## The dataset: `processed/posts.parquet`

One table, **7,954,297 posts**, all 15 subreddits, all dates (2008 → 2025).
Built 2026-07-02 from the raw dumps by `data_ingestion/scripts/prep_posts.py`
(no subreddit filter, no date filter). File size: 1.15 GB (zstd-compressed
parquet). Rows are stored in contiguous **subreddit blocks** (alphabetical by
source file), so filtering by subreddit is fast.

### Columns (schema)

| column | type | meaning |
|---|---|---|
| `id` | string | Reddit post id (e.g. `gpf44`) |
| `date` | string `YYYY-MM-DD` | UTC day the post was created (from `created_utc`) |
| `author` | string | username |
| `score` | int64 | upvotes at archive time (used for upvote-weighted counts) |
| `subreddit` | string | subreddit name, **lowercased** (e.g. `wallstreetbets`) |
| `title` | string | post title |
| `selftext` | string | post body (a comment's `body` would land here too) |
| `num_comments` | int64 | comment count at archive time |

This is exactly the schema `src/clean_data.py` produces, so every notebook and
script downstream works on it unchanged.

### Subreddits scraped — counts, date ranges, raw sizes

| subreddit | posts | first post | last post | raw .zst size |
|---|---:|---|---|---:|
| wallstreetbets | 2,278,119 | 2012-04-11 | 2025-12-31 | 600 MB |
| cryptocurrency | 1,715,219 | 2013-03-11 | 2025-12-31 | 650 MB |
| personalfinance | 1,383,818 | 2009-02-09 | 2025-12-31 | 676 MB |
| bitcoin | 1,164,734 | 2010-09-10 | 2025-12-31 | 414 MB |
| investing | 336,252 | 2008-03-15 | 2025-12-31 | 114 MB |
| stocks | 322,977 | 2008-06-27 | 2025-12-31 | 114 MB |
| pennystocks | 199,236 | 2008-12-31 | 2025-12-31 | 91 MB |
| stockmarket | 150,279 | 2008-07-09 | 2025-12-31 | 75 MB |
| options | 109,680 | 2009-10-03 | 2025-12-31 | 47 MB |
| daytrading | 89,512 | 2009-04-29 | 2025-12-31 | 77 MB |
| financialindependence | 88,134 | 2011-11-10 | 2025-12-30 | 33 MB |
| dividends | 37,074 | 2009-02-02 | 2025-12-31 | 29 MB |
| thetagang | 33,314 | 2019-11-01 | 2025-12-31 | 21 MB |
| securityanalysis | 27,155 | 2011-04-13 | 2025-11-17 | 9 MB |
| valueinvesting | 18,794 | 2010-09-29 | 2025-12-31 | 27 MB |
| **total** | **7,954,297** | 2008-03-15 | 2025-12-31 | **~2.98 GB** |

These are **submissions only** (posts with a title) — no comment dumps yet.

### How the extraction works (raw → parquet)

1. **Stream-decompress** each `.zst` (zstandard, long-distance window) — one
   JSON object per line, never loading a whole file into memory.
2. **Normalise** every record with `src/clean_data.py::normalise()` — pick the
   8 columns above, convert `created_utc` → `YYYY-MM-DD`, lowercase the
   subreddit, coerce score/num_comments to ints.
3. **Drop** records with no parseable date; malformed JSON lines are skipped.
4. **Write in batches** to zstd parquet (never holds more than ~250k rows in
   memory), one subreddit block after another.

No deduplication, no date filter, no subreddit filter was applied — the parquet
is a faithful 1:1 of the raw dumps in tidy form.

### Loading it

```python
import pandas as pd

# whole thing (needs ~8 GB RAM) …
df = pd.read_parquet("data/processed/posts.parquet")

# …or just the columns / subreddit you need (much lighter):
df = pd.read_parquet("data/processed/posts.parquet",
                     columns=["date", "subreddit", "title", "score"])
wsb = df[df.subreddit == "wallstreetbets"]
```

---

## raw/ — the source dumps

The 15 per-subreddit Pushshift/Academic-Torrents dumps
(`<name>_submissions.zst`), copied here untouched so the pipeline can be re-run
from scratch with different filters (see `data_ingestion/README.md`). ~2.98 GB total.

## reference/

`nasdaqlisted.txt` / `otherlisted.txt` — the official US ticker universe,
downloaded automatically by `src/ticker_universe.py` on first run.

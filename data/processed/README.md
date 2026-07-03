# data/processed/ — pipeline outputs

| file | what it is | size |
|---|---|---|
| `posts.parquet` | **The dataset.** All 15 subreddits, all dates, one row per post | 1,149,978,393 bytes (~1.15 GB) |
| `daily_ticker_counts.parquet` | Daily mention counts per ticker (output of notebook 02) | small |

---

## How `posts.parquet` is arranged

**7,954,297 rows, 8 columns, 34 row groups, zstd-compressed**
(written 2026-07-02 by `data_ingestion/scripts/prep_posts.py`).

### Row order = subreddit blocks

Rows are NOT interleaved. Each subreddit sits in one contiguous block, in the
ASCII sort order of the source filenames (capitals sort before lowercase):

```
Bitcoin → CryptoCurrency → Daytrading → SecurityAnalysis → StockMarket
→ ValueInvesting → dividends → financialindependence → investing → options
→ pennystocks → personalfinance → stocks → thetagang → wallstreetbets
```

Inside each block, posts keep the order of the raw dump, which is
chronological (oldest first). So the file reads like: all of r/Bitcoin
2010→2025, then all of r/CryptoCurrency 2013→2025, and so on.

### Row groups (why reads are fast)

Parquet stores rows in "row groups" — independently compressed chunks with
their own min/max statistics per column. This file has 34 of them (the largest
1,048,576 rows). Because subreddits are contiguous, a filter like
`subreddit == "daytrading"` lets pyarrow skip every row group whose stats say
the subreddit isn't in it — it reads ~1 of 34 groups instead of the whole GB.

```python
import pyarrow.parquet as pq

# one subreddit, three columns — loads in ~1 second:
df = pq.read_table(
    "data/processed/posts.parquet",
    columns=["date", "title", "score"],
    filters=[("subreddit", "=", "daytrading")],
).to_pandas()
```

### Columns

`id, date, author, score, subreddit, title, selftext, num_comments` — full
column documentation and per-subreddit row counts are in `data/README.md`.

### Integrity

The file was verified after creation: MD5-checked against the build, row count
and per-subreddit counts asserted in `tests/test_pipeline.py` (run `pytest
tests/ -v`; the tests will catch a stale or corrupted parquet).

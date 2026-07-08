# data_ingestion/ — data acquisition & ingestion tooling

**Why does this folder exist?** It is the *entry point* of the whole project:
everything downstream (notebooks, ticker counts, inflection detection) assumes
`data/processed/posts.parquet` exists. This folder holds the instructions for
**getting** the raw Reddit dumps and the **scripts that turn them into that
parquet file**. Delete it and you can still *analyse* data you already have —
but you can no longer re-download, re-filter, re-ingest, or explain where the
data came from.

> **Status (2026-07-02): ingestion is DONE.** 15 subreddit dumps (~2.98 GB)
> live in `data/raw/`, and the full merged dataset (7,954,297 posts, all
> dates, one `subreddit` column) is at `data/processed/posts.parquet`.
> See `data/README.md` for the dataset documentation. You only need the
> steps below to re-run or change the ingestion.

---

## The full data-ingestion pipeline

```
 STEP 0 (once)            STEP 1                       STEP 2
 torrent the dumps   ->   data/raw/*.zst          ->   data/processed/posts.parquet
 (qBittorrent,            15 per-subreddit             ONE tidy table:
  academictorrents)       Pushshift dumps              id, date, author, score,
                                                       subreddit, title, selftext,
                                                       num_comments
                              |                              |
                     prep_posts.py                   notebooks 02-05 read this
                     (clean + dedup)                 (mentions, themes, take-offs)
```

**Step 2 has two interchangeable scripts** — both reuse the project's own
`src/clean_data.py`, so the output schema is always identical:

| Script | Use when | Filters |
|---|---|---|
| `scripts/prep_posts.py` | You want a **filtered slice** (specific subs and/or a date window, e.g. the 2021 meme-stock period) | `START_DATE`, `END_DATE`, and the subreddit list in `finance_subreddits.txt` |
| `scripts/prep_posts.py` | You want **everything** (all subs, all dates). Streams + writes in batches so it never runs out of memory, even on 30M+ posts | none by default (edit the PARAMETERS block to add) |

```bash
# filtered slice (edit PARAMETERS at the top first):
python3 data_ingestion/scripts/prep_posts.py

# the full thing (this is what built the current posts.parquet):
python3 data_ingestion/scripts/prep_posts.py
```

Either way the result is `data/processed/posts.parquet`, and notebook 02 can
run immediately afterwards.

### How the cleaning itself works (inside `src/clean_data.py`)

1. Stream-decompress each `.zst` line by line (never loads a file into RAM).
2. Parse each line as JSON; skip malformed lines.
3. `normalise()` maps each record to the 8 standard columns
   (`created_utc` → `YYYY-MM-DD`, subreddit lowercased, ints coerced).
4. Optional filters: subreddit list, start date (inclusive), end date (exclusive).
5. Write parquet (zstd). `prep_posts.py` flushes every 250k rows, so the
   rows end up grouped in subreddit blocks.

---

## What's in this folder

| Item | What it is |
|------|------------|
| `finance_subreddits.txt` | Subreddit filter list used by `prep_posts.py` (now 17 names — Bitcoin and thetagang were added 2026-07-02) |
| `scripts/prep_posts.py` | Ingestion, filtered (subs + date window) |
| `scripts/prep_posts.py` | Ingestion, full dump, memory-safe batching |

**Downloaded so far** (submissions only, no comment dumps yet): wallstreetbets,
CryptoCurrency, personalfinance, Bitcoin, investing, stocks, pennystocks,
StockMarket, options, Daytrading, financialindependence, dividends, thetagang,
SecurityAnalysis, ValueInvesting. Still on the wishlist from the original 15:
`finance`, `Bogleheads` (no files in `data/raw/` yet — torrent them and re-run
step 2 to include them).

---

## Step 0 — (re-)downloading dumps

The per-subreddit torrent (each subreddit is its own file, so you only take
what you need — the full monthly archive is 3.8 TB, don't use that one):

https://academictorrents.com/details/3e3f64dee22dc304cdd2546254ca1f8e8ae542b4

1. Install **qBittorrent** (free): https://www.qbittorrent.org/download
2. Open the magnet link → in the "Add torrent" window, **Uncheck all**.
3. Tick the files you want, e.g. `finance_submissions.zst`
   (`_submissions` = posts; `_comments` = comments — names are
   capital-sensitive: `StockMarket`, `SecurityAnalysis`, `Bogleheads`).
4. Set the save path to this project's `data/raw/` and let it finish.
5. Re-run step 2 (one of the two prep scripts) to rebuild `posts.parquet`.

---

## Notes

- A `.zst` holds either *submissions* (have a `title`) or *comments* (have a
  `body`); the cleaner puts a comment's `body` into `selftext` so no text is
  lost — comments and posts can share one table.
- `data/processed/posts.parquet` is a faithful 1:1 of the raw dumps (no dedupe,
  no filters). Re-run a prep script with filters instead of editing the parquet.
- Source & citation: stuck_in_the_matrix, Watchful1, RaiderBDev — Reddit
  comments/submissions, Academic Torrents. Parsing adapted from
  https://github.com/Watchful1/PushshiftDumps

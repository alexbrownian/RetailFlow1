# GIC_RAW_DATA — the only data folder we commit

This folder is the **abstraction layer**: the *only* project data allowed onto
GitHub and the work laptop. It holds five small parquet files (~2 MB total) that
carry **no post text, no authors, no post ids, no subreddit names** — only daily
**counts** and **sentiment scores** per ticker / theme. You cannot reconstruct a
single Reddit / X / StockTwits post from them.

| file | columns |
|---|---|
| `daily_ticker_counts.parquet` | date, ticker, mention_count |
| `daily_ticker_counts_by_source.parquet` | date, ticker, source, mention_count |
| `daily_ticker_sentiment.parquet` | date, ticker, n_posts, avg_sentiment, net_bullish |
| `daily_theme_counts.parquet` | date, theme, mention_count |
| `daily_theme_sentiment.parquet` | date, theme, n_posts, avg_sentiment, net_bullish |

`source` keeps the readable labels `reddit` / `x` / `stocktwits`, but there is no
text attached to them — just numbers.

## Why the split works

The pipeline already turns text into numbers at a fixed line:

- **Producer side (needs raw text):** notebooks 01, 02, 04, 06, 07. They read the
  1 GB `posts.parquet` (which stays private, gitignored) and write the five
  aggregates above. Run these where the raw data lives.
- **Consumer side (numbers only):** notebooks 03, 05, 08, 09, 10 and the
  dashboard. They read only the five aggregates — so they run on the work laptop,
  which never sees a raw post.

## One-time bootstrap (producer machine, where `posts.parquet` lives)

Run the normal chain once, then publish the aggregates here:

```bash
python run_daily.py                     # (or run notebooks 01–07 by hand)
python -c "from src import gic_data; gic_data.export()"
git add GIC_RAW_DATA && git commit -m "publish abstracted aggregates"
```

`export()` copies the five files from `data/processed/` into this folder. That is
the **once** step — it seeds the committed history.

## On the work laptop (repeat as often as you ingest)

```bash
git pull                                        # get the latest GIC_RAW_DATA
python -c "from src import gic_data; gic_data.hydrate()"   # -> data/processed
python api_calls/fetch_all.py                   # fetch live raw (transient)
python api_calls/append_live_to_gic.py          # fold new posts into GIC_RAW_DATA
# then re-run notebooks 08/09/10 (or run_daily.py) and the dashboard
git add GIC_RAW_DATA && git commit -m "live update"   # push the new days back
```

`append_live_to_gic.py` aggregates the newly fetched posts and **merges** them
into the five files — counts add up, sentiment means recombine weighted by
`n_posts`, so history is never revised, only extended. It then discards the raw
text. A local, gitignored ledger (`data/reference/gic_live_meta.json`) remembers
which post ids were already folded in, so running it twice folds nothing the
second time (first-seen-wins).

## What is NOT here (on purpose)

`posts.parquet`, `posts_slice.parquet`, any `*.jsonl` / `*.zst` raw, and the
seen-ids ledger. `.gitignore` blocks all of these from this folder as a safety
net.

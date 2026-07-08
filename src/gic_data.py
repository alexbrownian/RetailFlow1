"""
gic_data.py
===========
GIC_RAW_DATA is the ONE thing we are allowed to commit to GitHub and pull onto
the work laptop. It holds NO post text, NO authors, NO post ids, NO subreddit
names - only daily COUNTS and SENTIMENT SCORES per ticker / theme. Five small
parquet files (~2 MB total) from which notebooks 03/05/08/09/10 and the
dashboard run with zero access to the underlying Reddit / X / StockTwits posts.

WHY THIS IS SAFE TO COMMIT
--------------------------
The 1 GB posts.parquet and posts_slice.parquet reveal everything (title,
selftext, author, id, subreddit). The five aggregate files below are what the
pipeline already produces AFTER the text has been turned into numbers - they
carry only (date, ticker/theme, counts, sentiment scores). You cannot rebuild a
single post from them. The source column keeps the readable labels
'reddit'/'x'/'stocktwits' (your choice) but there is no text attached to them.

THE FIVE FILES (exact schema the notebooks already write / read)
    daily_ticker_counts.parquet            date, ticker, mention_count
    daily_ticker_counts_by_source.parquet  date, ticker, source, mention_count
    daily_ticker_sentiment.parquet         date, ticker, n_posts, avg_sentiment, net_bullish
    daily_theme_counts.parquet             date, theme,  mention_count
    daily_theme_sentiment.parquet          date, theme,  n_posts, avg_sentiment, net_bullish

TWO JOBS THIS MODULE DOES
    1. export()  copy the five files data/processed -> GIC_RAW_DATA  (producer
       machine, after notebooks 01-07 have run on the raw data)
       hydrate()  copy the five files GIC_RAW_DATA -> data/processed  (work
       laptop, so the UNCHANGED consumer notebooks + dashboard find them where
       they already look). No notebook edits needed - we only copy across.

    2. aggregate_posts() + merge_into_gic()  fold a batch of NEW live posts into
       the committed aggregates WITHOUT keeping the posts. Counts simply ADD;
       sentiment means RECOMBINE weighted by n_posts. Both are proven to give
       the identical result you would get by aggregating everything in one shot
       (see tests/test_gic_merge.py).

WHY THE SENTIMENT MERGE IS WEIGHTED (the one bit of maths worth reading)
    avg_sentiment is a mean over posts, and net_bullish = (bulls - bears)/n is
    also a per-post mean. To combine an OLD day-row (n_old posts) with a NEW
    day-row (n_new posts) you cannot average the two averages - a row built
    from 100 posts must count more than a row built from 3. So we rebuild the
    underlying sums:
        combined_avg = (avg_old*n_old + avg_new*n_new) / (n_old + n_new)
    and the same for net_bullish. That is EXACTLY what one-shot aggregation
    would compute, so history never gets revised - it only accumulates.
"""

from __future__ import annotations

import os
import shutil

import pandas as pd

# ---------------------------------------------------------------------------
# WHERE THINGS LIVE
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIC_DIR = os.path.join(ROOT, "GIC_RAW_DATA")               # committed to git
PROCESSED_DIR = os.path.join(ROOT, "data", "processed")    # private, gitignored

# The five canonical filenames (used everywhere so there is one source of truth).
TICKER_COUNTS = "daily_ticker_counts.parquet"
TICKER_COUNTS_BY_SOURCE = "daily_ticker_counts_by_source.parquet"
TICKER_SENT = "daily_ticker_sentiment.parquet"
THEME_COUNTS = "daily_theme_counts.parquet"
THEME_SENT = "daily_theme_sentiment.parquet"

FILES = [TICKER_COUNTS, TICKER_COUNTS_BY_SOURCE, TICKER_SENT,
         THEME_COUNTS, THEME_SENT]

# For each file: how a merge combines it, and the columns that make a row unique.
#   "counts"    -> mention_count adds up
#   "sentiment" -> n_posts adds, means recombine weighted by n_posts
MERGE_RULES = {
    TICKER_COUNTS:            ("counts",    ["date", "ticker"]),
    TICKER_COUNTS_BY_SOURCE:  ("counts",    ["date", "ticker", "source"]),
    TICKER_SENT:              ("sentiment", ["date", "ticker"]),
    THEME_COUNTS:             ("counts",    ["date", "theme"]),
    THEME_SENT:               ("sentiment", ["date", "theme"]),
}


# ---------------------------------------------------------------------------
# COPY HELPERS - export (producer) and hydrate (consumer)
# ---------------------------------------------------------------------------
def _copy_files(src_dir, dst_dir, verbose):
    """Copy whichever of the five files exist from src_dir to dst_dir."""
    os.makedirs(dst_dir, exist_ok=True)
    copied = []
    for name in FILES:
        src_path = os.path.join(src_dir, name)
        if os.path.exists(src_path):
            shutil.copy2(src_path, os.path.join(dst_dir, name))
            copied.append(name)
            if verbose:
                size_kb = os.path.getsize(src_path) / 1024
                print(f"  copied {name:<40} ({size_kb:,.0f} KB)")
        elif verbose:
            print(f"  (skip, not found) {name}")
    return copied


def export(src_dir=PROCESSED_DIR, dst_dir=GIC_DIR, verbose=True):
    """Producer side: publish the five aggregates to GIC_RAW_DATA for commit."""
    if verbose:
        print(f"export: {src_dir} -> {dst_dir}")
    copied = _copy_files(src_dir, dst_dir, verbose)
    if verbose:
        print(f"export done: {len(copied)}/{len(FILES)} files in GIC_RAW_DATA")
    return copied


def hydrate(src_dir=GIC_DIR, dst_dir=PROCESSED_DIR, verbose=True):
    """Consumer side (work laptop): copy the committed aggregates into
    data/processed so the UNCHANGED notebooks 08/09/10 and the dashboard find
    them exactly where they already look."""
    if verbose:
        print(f"hydrate: {src_dir} -> {dst_dir}")
    copied = _copy_files(src_dir, dst_dir, verbose)
    if verbose:
        print(f"hydrate done: {len(copied)}/{len(FILES)} files in data/processed")
    return copied


# ---------------------------------------------------------------------------
# MERGE MATHS - the heart of "append without revising history"
# ---------------------------------------------------------------------------
def _normalise_date(df):
    """Make the date column a real datetime so grouping never treats the string
    '2021-01-01' and the Timestamp 2021-01-01 as two different days."""
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    return out


def merge_counts(old, new, keys):
    """Additive merge: same (date, ticker[, source]) rows have their
    mention_count summed. Brand-new rows are just carried through."""
    both = pd.concat([_normalise_date(old), _normalise_date(new)], ignore_index=True)
    merged = both.groupby(keys, as_index=False)["mention_count"].sum()
    return merged.sort_values(keys).reset_index(drop=True)


def merge_sentiment(old, new, keys):
    """n_posts-weighted merge (see the module docstring for why). Rebuilds the
    per-day sums, adds them, then divides back out."""
    def prep(df):
        df = _normalise_date(df)
        # avg_sentiment * n_posts = total compound score that day
        df["_sent_sum"] = df["avg_sentiment"] * df["n_posts"]
        # net_bullish * n_posts = (bulls - bears) that day
        df["_nb_sum"] = df["net_bullish"] * df["n_posts"]
        return df

    both = pd.concat([prep(old), prep(new)], ignore_index=True)
    grouped = both.groupby(keys, as_index=False).agg(
        n_posts=("n_posts", "sum"),
        _sent_sum=("_sent_sum", "sum"),
        _nb_sum=("_nb_sum", "sum"),
    )
    grouped["avg_sentiment"] = grouped["_sent_sum"] / grouped["n_posts"]
    grouped["net_bullish"] = grouped["_nb_sum"] / grouped["n_posts"]
    grouped = grouped.drop(columns=["_sent_sum", "_nb_sum"])
    # keep the columns in the schema order the notebooks expect
    entity = [k for k in keys if k != "date"]
    cols = ["date"] + entity + ["n_posts", "avg_sentiment", "net_bullish"]
    return grouped[cols].sort_values(keys).reset_index(drop=True)


def _safe_write(df, path):
    """Write a parquet the Windows-safe way: write a .tmp file, then swap it in.
    If the target is locked (open in Jupyter/Excel/the dashboard) we print the
    manual rename commands instead of leaving things half-written."""
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    try:
        if os.path.exists(path):
            os.remove(path)
        os.replace(tmp, path)
    except PermissionError:
        print("!" * 68)
        print(f"Could not replace {os.path.basename(path)} - it is open in")
        print("another program (a Jupyter kernel, Excel, or the dashboard).")
        print("Close it, then rename by hand:")
        print(f'  del "{path}"')
        print(f'  ren "{tmp}" "{os.path.basename(path)}"')
        print("!" * 68)
        raise


def merge_into_gic(new_aggs, gic_dir=GIC_DIR, verbose=True):
    """Fold a dict of {filename: new_aggregate_df} into GIC_RAW_DATA.
    Each file is read, merged by its rule, and written back. Files that already
    exist accumulate; files that don't are created."""
    os.makedirs(gic_dir, exist_ok=True)
    summary = {}
    for name, (kind, keys) in MERGE_RULES.items():
        new = new_aggs.get(name)
        if new is None or len(new) == 0:
            continue
        path = os.path.join(gic_dir, name)
        if os.path.exists(path):
            old = pd.read_parquet(path)
            if kind == "counts":
                merged = merge_counts(old, new, keys)
            else:
                merged = merge_sentiment(old, new, keys)
        else:
            merged = _normalise_date(new)
        _safe_write(merged, path)
        summary[name] = len(merged)
        if verbose:
            print(f"  merged {name:<40} -> {len(merged):,} rows")
    return summary


# ---------------------------------------------------------------------------
# AGGREGATION - turn a batch of posts into the five aggregate frames.
# Reuses the SAME functions the notebooks use, so live and historical numbers
# are produced by identical code (no second implementation to drift).
# ---------------------------------------------------------------------------
def _build_daily_theme_counts(posts_df):
    """date, theme, mention_count - each post counts once per theme it mentions
    (breadth of attention), same rule as build_daily_counts uses for tickers."""
    from src.themes import themes_in_text

    rows = []
    titles = posts_df["title"].fillna("").astype(str)
    bodies = posts_df["selftext"].fillna("").astype(str)
    dates = posts_df["date"].astype(str)
    for date, title, body in zip(dates, titles, bodies):
        for theme in set(themes_in_text(title + " " + body)):
            rows.append({"date": date, "theme": theme})
    if not rows:
        return pd.DataFrame(columns=["date", "theme", "mention_count"])
    long_df = pd.DataFrame(rows)
    daily = (long_df.groupby(["date", "theme"], as_index=False)
             .size().rename(columns={"size": "mention_count"}))
    return daily


def load_universe():
    """The valid US ticker set (cached Nasdaq files + delisted supplement).
    max_cache_age_days is huge so this never tries to hit the network on the
    work laptop - the cache under data/reference is enough."""
    from pathlib import Path
    from src.ticker_universe import load_us_ticker_universe
    return load_us_ticker_universe(Path(ROOT) / "data" / "reference",
                                   max_cache_age_days=100000)


def aggregate_posts(posts_df, universe=None, cashtags_only=False):
    """posts_df: standard 9-column posts (needs date, title, selftext, source).
    Returns {filename: aggregate_df} for the five GIC files.

    Uses build_mentions + sentiment exactly like notebooks 02/06/07 do."""
    from src.build_mentions import build_daily_counts
    from src.sentiment import (add_sentiment_fast,
                               build_daily_ticker_sentiment,
                               build_daily_theme_sentiment)

    if universe is None:
        universe = load_universe()

    # ---- ticker counts: per source first, then sum into the combined signal
    parts = []
    for source_name in sorted(posts_df["source"].unique()):
        one = posts_df[posts_df["source"] == source_name]
        d = build_daily_counts(one, universe, cashtags_only=cashtags_only)
        d["source"] = source_name
        parts.append(d)
    if parts:
        by_source = pd.concat(parts, ignore_index=True)
    else:
        by_source = pd.DataFrame(columns=["date", "ticker", "mention_count", "source"])
    counts = by_source.groupby(["date", "ticker"], as_index=False)["mention_count"].sum()

    # ---- sentiment: score once, then roll up per ticker and per theme
    posts_scored = add_sentiment_fast(posts_df)
    ticker_sent = build_daily_ticker_sentiment(posts_scored, universe,
                                               cashtags_only=cashtags_only)
    theme_sent = build_daily_theme_sentiment(posts_scored)
    theme_counts = _build_daily_theme_counts(posts_df)

    return {
        TICKER_COUNTS: counts,
        TICKER_COUNTS_BY_SOURCE: by_source[["date", "ticker", "source", "mention_count"]],
        TICKER_SENT: ticker_sent,
        THEME_COUNTS: theme_counts,
        THEME_SENT: theme_sent,
    }

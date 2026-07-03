"""
build_mentions.py
=================
Bridge between the cleaned posts and the analysis notebooks.

It takes the tidy posts table (from clean_data.py), runs the ticker extractor
on each post's title + selftext, and returns a daily count table:

    date, ticker, mention_count

That table is exactly what both notebooks (mentions-over-time and
first-derivative) expect. We keep the heavy ticker logic in extract_tickers.py
and the valid-symbol list in ticker_universe.py - this file just wires them
together so a notebook can do it in one line.
"""

import pandas as pd

from .extract_tickers import extract_tickers_from_text


def build_daily_counts(posts_df, universe, cashtags_only=False):
    """
    posts_df : DataFrame with columns date, title, selftext, score
    universe : set of valid ticker symbols (from load_us_ticker_universe)
    cashtags_only : True = only count $TICKER (cleaner, fewer false hits)

    Returns a DataFrame with columns:
        date, ticker, mention_count, weighted_count

      mention_count  = number of distinct posts that mention the ticker that day
                       (each post counted once regardless of how many times
                       the ticker appears in it)
      weighted_count = sum of score² across all posts mentioning the ticker.
                       Squaring the upvote score means a 1,000-upvote post
                       contributes 1,000,000 vs a 10-upvote post's 100 —
                       viral posts dominate. Each post is counted once per
                       ticker (no inflation from repeated in-post mentions).

    Why two numbers? Raw counts treat every post equally - easy to spam with
    bots/low-effort posts. Weighting by score² leans heavily on what the
    crowd pushed up, amplifying genuine viral signals over noise.
    """
    # If there is no score column (e.g. some CSV exports), treat every post as 0.
    have_score = "score" in posts_df.columns

    rows = []
    titles = posts_df["title"].fillna("").astype(str)
    bodies = posts_df["selftext"].fillna("").astype(str)
    dates = posts_df["date"].astype(str)
    scores = posts_df["score"].fillna(0).astype(int) if have_score else [0] * len(posts_df)

    for date, title, body, score in zip(dates, titles, bodies, scores):
        text = title + " " + body
        # set() deduplicates so each ticker contributes at most once per post.
        for ticker in set(extract_tickers_from_text(text, universe, cashtags_only=cashtags_only)):
            rows.append({"date": date, "ticker": ticker, "score": score})

    if not rows:
        return pd.DataFrame(columns=["date", "ticker", "mention_count", "weighted_count"])

    long_df = pd.DataFrame(rows)
    grouped = long_df.groupby(["date", "ticker"])
    daily = grouped.agg(
        mention_count=("ticker", "size"),
        weighted_count=("score", lambda s: (s ** 2).sum()),
    ).reset_index()
    return daily

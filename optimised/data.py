"""
optimised.data - loaders and base frames
========================================
Every loader returns None when its file is missing (the dashboard shows a
"run notebook X" note instead of crashing), and takes the file's mtime as
an argument so Streamlit's cache invalidates the moment the pipeline
rewrites a file. Loading is column-pruned; the daily parquets are tiny
(< 5 MB), so everything here is effectively instant.
"""

from __future__ import annotations

import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "data", "processed")

PATHS = {
    "counts": os.path.join(P, "daily_ticker_counts.parquet"),           # nb 02
    "by_source": os.path.join(P, "daily_ticker_counts_by_source.parquet"),
    "tick_sent": os.path.join(P, "daily_ticker_sentiment.parquet"),     # nb 06
    "theme_sent": os.path.join(P, "daily_theme_sentiment.parquet"),     # nb 07
    "signals": os.path.join(P, "trade_signals.parquet"),                # nb 09
    "signals_tickers": os.path.join(P, "trade_signals_tickers.parquet"),
}


def mtime(name: str) -> float | None:
    """Cache key helper: the file's modification time (None if absent)."""
    path = PATHS[name]
    return os.path.getmtime(path) if os.path.exists(path) else None


def load(name: str, date_col: str = "date") -> pd.DataFrame | None:
    """Read one parquet with a proper datetime 'date' column."""
    path = PATHS[name]
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df[date_col])
    return df


# ---------------------------------------------------------------------
# ticker -> theme rollup (the notebook-09 mapping, fully vectorised:
# one static lookup table + one merge instead of a per-row map/explode)
# ---------------------------------------------------------------------
def _lookup_frame() -> pd.DataFrame:
    from src.themes import THEME_TICKERS
    rows = [(ticker, theme)
            for theme, tickers in THEME_TICKERS.items()
            for ticker in tickers]
    return pd.DataFrame(rows, columns=["ticker", "theme"])


def theme_mentions_wide(counts: pd.DataFrame,
                        min_daily_mentions: float = 3.0) -> pd.DataFrame:
    """Daily theme mention matrix (rows = every calendar day, cols = themes),
    built from the ticker counts. A ticker feeds all its themes."""
    merged = counts.merge(_lookup_frame(), on="ticker", how="inner")
    daily = merged.groupby(["date", "theme"])["mention_count"].sum().reset_index()
    all_days = pd.date_range(counts["date"].min(), counts["date"].max(), freq="D")
    wide = (daily.pivot_table(index="date", columns="theme", values="mention_count")
            .reindex(all_days).fillna(0.0))
    return wide.loc[:, wide.mean() >= min_daily_mentions]


def theme_sent_wide(theme_sent: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(n_posts, net_bullish) daily matrices for the themes."""
    all_days = pd.date_range(theme_sent["date"].min(), theme_sent["date"].max(), freq="D")
    n = (theme_sent.pivot_table(index="date", columns="theme", values="n_posts")
         .reindex(all_days).fillna(0.0))
    s = (theme_sent.pivot_table(index="date", columns="theme", values="net_bullish")
         .reindex(all_days))
    return n, s


def ticker_wide(counts: pd.DataFrame) -> pd.DataFrame:
    """Daily mention matrix per ticker (for velocity / top tables)."""
    all_days = pd.date_range(counts["date"].min(), counts["date"].max(), freq="D")
    return (counts.pivot_table(index="date", columns="ticker",
                               values="mention_count", aggfunc="sum")
            .reindex(all_days).fillna(0.0))

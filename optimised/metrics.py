"""
optimised.metrics - the maths, vectorised
=========================================
Every function here works on WHOLE matrices at once (days x names) - the
same trailing-baseline maths as notebooks 03/05/06/07/08/09, but never a
Python loop over rows. Trailing baselines only (live-parity rule): a value
on day t is computable from data <= t, so window changes never distort it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

K = 2.0            # default take-off threshold (calibrated later vs prices)
ROLL = 7           # rolling sum window (days)
BASELINE = 84      # trailing baseline length (days)
MIN_DAYS = 28      # warm-up before a z exists


def trailing_z(wide: pd.DataFrame, roll: int = ROLL, baseline: int = BASELINE,
               min_days: int = MIN_DAYS) -> pd.DataFrame:
    """7d rolling sum, z-scored vs each column's OWN trailing history.
    The first-derivative/take-off signal of notebooks 03/05 in one call."""
    r = wide.rolling(roll, min_periods=1).sum()
    mu = r.rolling(baseline, min_periods=min_days).mean()
    sd = r.rolling(baseline, min_periods=min_days).std()
    return (r - mu) / sd.replace(0.0, np.nan)


def conviction_z(n_wide: pd.DataFrame, s_wide: pd.DataFrame,
                 **kw) -> pd.DataFrame:
    """Notebook 08's composite: bull pressure = posts x net_bullish
    (bullish-minus-bearish POST COUNT), trailing-z'd like attention."""
    pressure = (n_wide * s_wide).fillna(0.0)
    return trailing_z(pressure, **kw)


def rolled_sentiment(theme_sent: pd.DataFrame, roll: int = ROLL,
                     min_posts: float = 3.0) -> pd.DataFrame:
    """Notebook 07's JPM-style lines: per-theme rolling net-bullish share,
    thin days masked BEFORE smoothing. Returns a days x themes matrix."""
    from .data import theme_sent_wide
    n, s = theme_sent_wide(theme_sent)
    masked = s.where(n >= min_posts)
    return masked.rolling(roll, min_periods=max(2, roll // 2)).mean()


def velocity_z(wide: pd.DataFrame, last_days: int = 5) -> pd.Series:
    """Day-over-day change of the EWMA (span=ROLL) of mentions - the
    derivative is taken on the SMOOTHED line, never on raw mentions
    (same rule as src/inflection.py). z per column, averaged over the
    final `last_days`. 'Fastest risers' in one number per name."""
    velo = wide.ewm(span=ROLL, min_periods=1).mean().diff()
    z = (velo - velo.mean()) / velo.std().replace(0.0, np.nan)
    return z.iloc[-last_days:].mean().dropna().sort_values(ascending=False)


def momentum_stats(n_wide: pd.DataFrame, s_wide: pd.DataFrame,
                   min_posts_total: float = 50, last_days: int = 5) -> pd.DataFrame:
    """The momentum-map coordinates (notebooks 06/07), computed for every
    column at once: x = last-5d volume vs own window baseline (z),
    y = last-5d sentiment minus own window average (the change - cancels
    the retail bullish-level bias), plus bubble size and 5d lean."""
    keep = n_wide.columns[n_wide.sum() >= min_posts_total]
    n, s = n_wide[keep], s_wide.reindex(columns=keep)
    sd = n.std().replace(0.0, np.nan)
    lookback_z = (n.iloc[-last_days:].mean() - n.mean()) / sd
    lean_5d = s.iloc[-last_days:].mean()
    sent_delta = lean_5d - s.mean()
    out = pd.DataFrame({"posts": n.sum().astype(int),
                        "momentum_z": lookback_z.round(2),
                        "sent_change": sent_delta.round(3),
                        "lean_5d": lean_5d.round(2)})
    return out.dropna().reset_index(names="name")


def weekly_net(theme_sent: pd.DataFrame, themes: list[str],
               min_posts_week: int = 10) -> pd.DataFrame:
    """Notebook 08's heatmap table: post-weighted weekly net-bullish per
    theme; weeks under the post floor dropped. Long format for Altair."""
    hm = theme_sent[theme_sent["theme"].isin(themes)].copy()
    hm["week"] = hm["date"].dt.to_period("W").dt.start_time
    hm["w_net"] = hm["net_bullish"] * hm["n_posts"]
    wk = (hm.groupby(["week", "theme"])
          .agg(w=("w_net", "sum"), n=("n_posts", "sum")).reset_index())
    wk = wk[wk["n"] >= min_posts_week].copy()
    wk["net"] = wk["w"] / wk["n"]
    return wk[["week", "theme", "net"]]


def top_tickers(counts: pd.DataFrame, tick_sent: pd.DataFrame | None,
                n: int = 12) -> pd.DataFrame:
    """Notebook 06's monitor condensed: top names by mentions, with their
    5d velocity z and (if sentiment exists) 5d net-bullish lean."""
    from .data import ticker_wide
    wide = ticker_wide(counts)
    mentions = wide.sum().astype(int)
    vz = velocity_z(wide)
    out = pd.DataFrame({"mentions": mentions}).join(vz.rename("velocity_z"))
    if tick_sent is not None and len(tick_sent):
        lean = (tick_sent.sort_values("date").groupby("ticker")
                .apply(lambda g: g["net_bullish"].iloc[-5:].mean()))
        out = out.join(lean.rename("lean_5d"))
    out = out.sort_values("mentions", ascending=False).head(n).round(2)
    return out.reset_index(names="ticker")

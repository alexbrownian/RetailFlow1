"""
optimised.charts - Altair builders for the dashboard
====================================================
Each function takes ready-made frames from optimised.metrics and returns
an Altair chart - no computation happens here, so the dashboard stays a
thin display layer. Colour language is consistent everywhere:
green = bullish / rising, red = bearish, dashed red = the K threshold.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

SENT_SCALE = alt.Scale(scheme="redyellowgreen", domain=[-1, 1])


def takeoff_chart(att_z: pd.DataFrame, themes: list[str], k: float = 2.0,
                  height: int = 320) -> alt.Chart:
    """Notebook 05's first-derivative view: per-theme attention z with the
    take-off threshold. Above the dashed line = statistically unusual crowd."""
    plot = (att_z[themes].reset_index(names="date")
            .melt("date", var_name="theme", value_name="z").dropna())
    lines = alt.Chart(plot).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("z:Q", title="attention z (trailing baseline)"),
        color=alt.Color("theme:N", legend=alt.Legend(orient="bottom")),
        tooltip=["date:T", "theme:N", alt.Tooltip("z:Q", format="+.2f")])
    rule = alt.Chart(pd.DataFrame({"y": [k]})).mark_rule(
        strokeDash=[6, 4], color="#DC2626").encode(y="y:Q")
    return (lines + rule).properties(height=height).interactive()


def sentiment_lines(rolled: pd.DataFrame, themes: list[str],
                    etfs: dict[str, str], height: int = 320) -> alt.Chart:
    """Notebook 07's JPM-style chart: rolling net-bullish per theme,
    each line labelled with its tradeable ETF anchor."""
    plot = (rolled[themes]
            .rename(columns={t: f"{t} ({etfs.get(t, '?')})" for t in themes})
            .reset_index(names="date")
            .melt("date", var_name="theme", value_name="net_bullish").dropna())
    lines = alt.Chart(plot).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("net_bullish:Q", title="net bullish (-1..+1)",
                scale=alt.Scale(domain=[-1, 1])),
        color=alt.Color("theme:N", legend=alt.Legend(orient="bottom")),
        tooltip=["date:T", "theme:N", alt.Tooltip("net_bullish:Q", format="+.2f")])
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#94A3B8").encode(y="y:Q")
    return (lines + zero).properties(height=height).interactive()


def momentum_map(stats: pd.DataFrame, label_etfs: dict[str, str] | None = None,
                 height: int = 420) -> alt.Chart:
    """Notebooks 06/07's momentum map: x = attention momentum, y = sentiment
    CHANGE vs own average, bubble = posts, colour = absolute 5d lean."""
    m = stats.copy()
    if label_etfs:
        m["name"] = m["name"].map(lambda t: f"{t} ({label_etfs.get(t, '?')})")
    base = alt.Chart(m).encode(
        x=alt.X("momentum_z:Q", title="mention momentum (z, last 5d vs baseline)"),
        y=alt.Y("sent_change:Q", title="sentiment change vs own average"),
        tooltip=["name", "posts", "momentum_z", "sent_change", "lean_5d"])
    pts = base.mark_circle(opacity=.6, stroke="#0F172A", strokeWidth=.5).encode(
        size=alt.Size("posts:Q", legend=None),
        color=alt.Color("lean_5d:Q", scale=SENT_SCALE,
                        legend=alt.Legend(title="5d lean")))
    lbl = base.mark_text(dy=-12, fontSize=11).encode(text="name:N")
    return (pts + lbl).properties(height=height).interactive()


def conviction_heat(weekly: pd.DataFrame, height_per_row: int = 32) -> alt.Chart:
    """Notebook 08's heatmap: theme x week, colour = weekly net-bullish."""
    n_rows = weekly["theme"].nunique()
    return alt.Chart(weekly).mark_rect(cornerRadius=3).encode(
        x=alt.X("week:T", title=None),
        y=alt.Y("theme:N", title=None),
        color=alt.Color("net:Q", scale=alt.Scale(scheme="redyellowgreen",
                                                 domain=[-0.6, 0.6]),
                        legend=alt.Legend(title="net bullish")),
        tooltip=["week:T", "theme:N", alt.Tooltip("net:Q", format="+.2f")]
    ).properties(height=40 + height_per_row * n_rows)


def conviction_lines(conv_z: pd.DataFrame, themes: list[str], k: float = 2.0,
                     height: int = 300) -> alt.Chart:
    """Notebook 08's conviction z over time (volume x direction vs own
    history) with +K/-K entry/exit thresholds."""
    plot = (conv_z[themes].reset_index(names="date")
            .melt("date", var_name="theme", value_name="z").dropna())
    lines = alt.Chart(plot).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("z:Q", title="conviction z"),
        color=alt.Color("theme:N", legend=alt.Legend(orient="bottom")),
        tooltip=["date:T", "theme:N", alt.Tooltip("z:Q", format="+.2f")])
    rules = alt.Chart(pd.DataFrame({"y": [k, -k]})).mark_rule(
        strokeDash=[6, 4], color="#DC2626").encode(y="y:Q")
    return (lines + rules).properties(height=height).interactive()

"""
Retail Flow Tracker - dashboard
===============================
Live view over the REAL derived data the notebooks produce (no synthetic
data anywhere: every panel reads a parquet from data/processed/ and shows
an instruction instead if that file hasn't been generated yet).

Run from the project root:
    pip install streamlit
    streamlit run dashboard/app.py

The TIMEFRAME picker in the sidebar re-filters every panel instantly -
all metrics (top mentions, velocity, sentiment, momentum map) are
recomputed for the selected window only. To get NEW data in, re-run the
notebook chain (01 -> 02 -> 06/07); the dashboard picks up saved files
automatically (file changes invalidate the cache).

Panels:
  - KPI row: posts, mentions, market net-bullish, top riser
  - Top mentions + top VELOCITY change (day-over-day mention change z)
  - Market sentiment over time (all scored posts) + per-theme lines
  - Momentum map (attention z vs sentiment change) - interactive
  - Reddit vs X mention split (if by-source file exists)
"""

import os

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Retail Flow Tracker", layout="wide")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "data", "processed")

FILES = {
    "counts": os.path.join(P, "daily_ticker_counts.parquet"),          # notebook 02
    "by_source": os.path.join(P, "daily_ticker_counts_by_source.parquet"),
    "tick_sent": os.path.join(P, "daily_ticker_sentiment.parquet"),    # notebook 06
    "theme_sent": os.path.join(P, "daily_theme_sentiment.parquet"),    # notebook 07
}


@st.cache_data
def load(path, mtime):
    """mtime is part of the cache key, so a re-run of a notebook
    automatically refreshes the dashboard."""
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df


def maybe(name):
    path = FILES[name]
    if not os.path.exists(path):
        return None
    return load(path, os.path.getmtime(path))


counts = maybe("counts")
tick_sent = maybe("tick_sent")
theme_sent = maybe("theme_sent")
by_source = maybe("by_source")

st.title("Retail Flow Tracker")
if counts is None:
    st.error("No data yet: run notebooks 01 and 02 first "
             "(daily_ticker_counts.parquet is the minimum input).")
    st.stop()

# ---------------- sidebar: THE timeframe control ----------------
lo, hi = counts["date"].min().date(), counts["date"].max().date()
st.sidebar.header("Timeframe")
preset = st.sidebar.radio("Quick select", ["All data", "Last 90 days", "Last 30 days", "Custom"])
if preset == "Last 90 days":
    start, end = max(lo, hi - pd.Timedelta(days=90)), hi
elif preset == "Last 30 days":
    start, end = max(lo, hi - pd.Timedelta(days=30)), hi
elif preset == "Custom":
    start, end = st.sidebar.slider("Range", min_value=lo, max_value=hi, value=(lo, hi))
else:
    start, end = lo, hi
st.sidebar.caption(f"showing {start} to {end}")
TOP_N = st.sidebar.number_input("Rows in top lists", 5, 50, 10)


def clip(df):
    if df is None:
        return None
    return df[(df["date"].dt.date >= start) & (df["date"].dt.date <= end)]


c = clip(counts)
ts = clip(tick_sent)
th = clip(theme_sent)
bs = clip(by_source)

if c.empty:
    st.warning("No rows in this timeframe.")
    st.stop()

# ---------------- KPI row ----------------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Ticker mentions", f"{int(c['mention_count'].sum()):,}")
k2.metric("Tickers active", f"{c['ticker'].nunique():,}")
if ts is not None and not ts.empty:
    market_net = (ts["net_bullish"] * ts["n_posts"]).sum() / ts["n_posts"].sum()
    k3.metric("Market net-bullish", f"{market_net:+.2f}",
              help="post-weighted average of daily net_bullish across all tickers; "
                   "retail skews bullish, so compare across time, not to zero")
else:
    k3.metric("Market net-bullish", "run nb 06")

# velocity: day-over-day change of 7d-rolling mentions, z vs the window
wide = (c.pivot_table(index="date", columns="ticker", values="mention_count", aggfunc="sum")
        .asfreq("D").fillna(0))
roll = wide.rolling(7, min_periods=1).sum()
velo = roll.diff()
velo_z = (velo - velo.mean()) / velo.std().replace(0, pd.NA)
last_velo = velo_z.iloc[-5:].mean().dropna().sort_values(ascending=False)
if len(last_velo):
    k4.metric("Top velocity riser (5d)", last_velo.index[0], f"{last_velo.iloc[0]:+.1f}z")

st.divider()

# ---------------- top mentions & top velocity ----------------
left, right = st.columns(2)
with left:
    st.subheader("Top mentions")
    top_m = (c.groupby("ticker")["mention_count"].sum()
             .sort_values(ascending=False).head(int(TOP_N)).rename("mentions").reset_index())
    st.dataframe(top_m, hide_index=True, use_container_width=True)
with right:
    st.subheader("Top velocity change (last 5d, z)")
    st.caption("day-over-day change of 7d rolling mentions, z-scored per ticker "
               "within the selected window - the notebook-03 'take-off' idea")
    vt = last_velo.head(int(TOP_N)).rename("velocity_z").reset_index()
    vt.columns = ["ticker", "velocity_z"]
    st.dataframe(vt.round(2), hide_index=True, use_container_width=True)

# mention trend of the top tickers
st.subheader("Mentions over time (top tickers, 7d rolling)")
sel = top_m["ticker"].head(6).tolist()
trend = roll[sel].reset_index().melt("date", var_name="ticker", value_name="mentions_7d")
st.altair_chart(
    alt.Chart(trend).mark_line().encode(
        x="date:T", y="mentions_7d:Q", color="ticker:N",
        tooltip=["date:T", "ticker:N", "mentions_7d:Q"]).interactive(),
    use_container_width=True)

st.divider()

# ---------------- sentiment panels ----------------
st.subheader("Market sentiment (JPM-style)")
if ts is None or ts.empty:
    st.info("Run notebook 06 to unlock sentiment panels "
            "(daily_ticker_sentiment.parquet not found for this window).")
else:
    daily_mkt = (ts.assign(w=ts["net_bullish"] * ts["n_posts"])
                 .groupby("date").agg(w=("w", "sum"), n=("n_posts", "sum")))
    mkt = (daily_mkt["w"] / daily_mkt["n"]).rolling(7, min_periods=2).mean().rename("net_bullish")
    st.caption("post-weighted net-bullish across ALL tickers, 7d rolling; "
               "levels skew bullish - watch the changes")
    st.line_chart(mkt)

    if th is not None and not th.empty:
        st.subheader("Theme sentiment (7d rolling)")
        top_themes = (th.groupby("theme")["n_posts"].sum()
                      .sort_values(ascending=False).head(6).index.tolist())
        rows = []
        for theme in top_themes:
            one = (th[th["theme"] == theme].set_index("date")
                   .reindex(pd.date_range(start, end, freq="D")))
            line = one["net_bullish"].rolling(7, min_periods=2).mean()
            rows.append(pd.DataFrame({"date": line.index, "theme": theme, "net_bullish": line.values}))
        st.altair_chart(
            alt.Chart(pd.concat(rows)).mark_line().encode(
                x="date:T", y=alt.Y("net_bullish:Q", scale=alt.Scale(domain=[-1, 1])),
                color="theme:N", tooltip=["date:T", "theme:N", "net_bullish:Q"]).interactive(),
            use_container_width=True)

    # ---------------- momentum map ----------------
    st.subheader("Momentum map - last 5 days of the selected window")
    st.caption("x = mention momentum (last 5d vs window baseline, z) | "
               "y = sentiment change (last 5d minus window average) | size = posts")
    wn = (ts.pivot_table(index="date", columns="ticker", values="n_posts")
          .asfreq("D").fillna(0))
    ws = ts.pivot_table(index="date", columns="ticker", values="net_bullish").asfreq("D")
    rows = []
    for ticker in wn.columns:
        n = wn[ticker]
        if n.sum() < 25:
            continue
        std = n.std()
        lz = (n.iloc[-5:].mean() - n.mean()) / std if std > 0 else 0.0
        s5, sb = ws[ticker].iloc[-5:].mean(), ws[ticker].mean()
        if pd.isna(s5) or pd.isna(sb):
            continue
        rows.append({"ticker": ticker, "posts": int(n.sum()),
                     "mention_momentum_z": round(float(lz), 2),
                     "sent_change": round(float(s5 - sb), 3),
                     "sent_level_5d": round(float(s5), 2)})
    mm = pd.DataFrame(rows).sort_values("posts", ascending=False).head(30)
    if mm.empty:
        st.info("Not enough sentiment data in this timeframe for the map.")
    else:
        base = alt.Chart(mm).encode(
            x=alt.X("mention_momentum_z:Q", title="mention momentum (z)"),
            y=alt.Y("sent_change:Q", title="sentiment change vs window avg"),
            tooltip=["ticker", "posts", "mention_momentum_z", "sent_change", "sent_level_5d"])
        points = base.mark_circle(opacity=0.55).encode(
            size=alt.Size("posts:Q", legend=None),
            color=alt.Color("sent_level_5d:Q", scale=alt.Scale(scheme="redyellowgreen", domain=[-1, 1])))
        labels = base.mark_text(dy=-10, fontSize=10).encode(text="ticker:N")
        st.altair_chart((points + labels).interactive(), use_container_width=True)

# ---------------- reddit vs x ----------------
if bs is not None and not bs.empty and bs["source"].nunique() > 1:
    st.divider()
    st.subheader("Reddit vs X - daily mentions by source")
    split = (bs.groupby(["date", "source"])["mention_count"].sum().reset_index())
    st.altair_chart(
        alt.Chart(split).mark_line().encode(
            x="date:T", y="mention_count:Q", color="source:N",
            tooltip=["date:T", "source:N", "mention_count:Q"]).interactive(),
        use_container_width=True)

st.divider()
st.caption("Data sources: daily_ticker_counts.parquet (nb 02), daily_ticker_sentiment.parquet "
           "(nb 06), daily_theme_sentiment.parquet (nb 07), daily_ticker_counts_by_source.parquet "
           "(nb 02). Re-run the notebooks to refresh; nothing here is synthetic.")

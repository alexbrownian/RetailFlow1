"""
Retail Flow Tracker - investor dashboard
========================================
Single output surface for the whole pipeline. Reads ONLY the notebooks'
saved parquets (no synthetic data; panels explain what to run if their
input is missing). Charts are chosen for an investor's questions, drawn
from notebooks 05/07/08/09:

  - What would the model trade?      -> Signals blotter (09)
  - Which themes are taking off?     -> attention z vs threshold (03/05 logic)
  - How does the crowd feel?         -> theme sentiment lines (07, JPM-style)
  - How did mood evolve per theme?   -> weekly sentiment heatmap (08)
  - Where is attention vs mood NOW?  -> momentum map (06/07)

Run:  streamlit run dashboard/app.py     (from the project root)
Timeframe: pills at the top; "Latest 30d" anchors to the newest data in
the files (= live once run_daily.py is scheduled). The status strip shows
whether data is LIVE and when it was last refreshed.
"""

import datetime
import os
import sys

import altair as alt
import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.themes import THEME_ETFS, build_ticker_to_themes  # noqa: E402

P = os.path.join(ROOT, "data", "processed")

st.set_page_config(page_title="Retail Flow Tracker", page_icon="📡", layout="wide")

# ---- glassy light theme (cards, soft shadows, subtle blur) ----
st.markdown("""
<style>
.stApp { background: linear-gradient(180deg,#F6F9FE 0%,#EEF3FA 100%); }
[data-testid="stMetric"], .stDataFrame, [data-testid="stExpander"] {
  background: rgba(255,255,255,.72); backdrop-filter: blur(8px);
  border: 1px solid rgba(15,23,42,.07); border-radius: 14px;
  box-shadow: 0 6px 18px rgba(15,23,42,.05); padding: .6rem .8rem;
}
[data-testid="stMetricValue"] { font-weight: 700; }
h1,h2,h3 { letter-spacing:-.015em; color:#0F172A; }
hr { border-color: rgba(15,23,42,.08); }
.status-chip { display:inline-block; padding:2px 12px; border-radius:999px;
  font-size:.8rem; font-weight:600; margin-right:8px; }
.live  { background:#DCFCE7; color:#166534; border:1px solid #86EFAC; }
.hist  { background:#FEF3C7; color:#92400E; border:1px solid #FCD34D; }
.muted { background:#E2E8F0; color:#334155; }
</style>""", unsafe_allow_html=True)

FILES = {
    "counts": os.path.join(P, "daily_ticker_counts.parquet"),
    "by_source": os.path.join(P, "daily_ticker_counts_by_source.parquet"),
    "tick_sent": os.path.join(P, "daily_ticker_sentiment.parquet"),
    "theme_sent": os.path.join(P, "daily_theme_sentiment.parquet"),
    "signals": os.path.join(P, "trade_signals.parquet"),
    "signals_tickers": os.path.join(P, "trade_signals_tickers.parquet"),
}


@st.cache_data
def load(path, mtime, date_col="date"):
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df[date_col])
    return df


def maybe(name, date_col="date"):
    path = FILES[name]
    if not os.path.exists(path):
        return None
    return load(path, os.path.getmtime(path), date_col)


counts = maybe("counts")
theme_sent = maybe("theme_sent")
tick_sent = maybe("tick_sent")
by_source = maybe("by_source")
signals = maybe("signals", "signal_date")
signals_t = maybe("signals_tickers", "signal_date")

st.title("📡 Retail Flow Tracker")
if counts is None:
    st.error("No data yet - run notebooks 01 and 02 first.")
    st.stop()

# ---------------- status strip: is this LIVE? when refreshed? ----------------
data_max = counts["date"].max().date()
days_stale = (datetime.date.today() - data_max).days
newest_mtime = max(os.path.getmtime(p) for p in FILES.values() if os.path.exists(p))
refreshed = datetime.datetime.fromtimestamp(newest_mtime).strftime("%Y-%m-%d %H:%M")
log_dir = os.path.join(ROOT, "logs")
last_run = sorted(os.listdir(log_dir))[-1].replace("run_", "").replace(".log", "") \
    if os.path.isdir(log_dir) and os.listdir(log_dir) else "never"

chip = ('<span class="status-chip live">● LIVE - data through today</span>'
        if days_stale <= 2 else
        f'<span class="status-chip hist">◆ HISTORICAL - data ends {data_max} '
        f'({days_stale} days ago)</span>')
st.markdown(chip
            + f'<span class="status-chip muted">files refreshed {refreshed}</span>'
            + f'<span class="status-chip muted">last pipeline run: {last_run}</span>',
            unsafe_allow_html=True)

# ---------------- timeframe: one obvious control ----------------
lo, hi = counts["date"].min().date(), data_max
preset = st.radio("Timeframe",
                  ["Latest 30d", "Latest 90d", "Latest 12m", "All data", "Custom"],
                  horizontal=True, index=1,
                  help="'Latest' anchors to the newest data in the files - "
                       "with the daily pipeline scheduled, that IS live/now.")
if preset == "Latest 30d":
    start, end = max(lo, hi - pd.Timedelta(days=30)), hi
elif preset == "Latest 90d":
    start, end = max(lo, hi - pd.Timedelta(days=90)), hi
elif preset == "Latest 12m":
    start, end = max(lo, hi - pd.Timedelta(days=365)), hi
elif preset == "Custom":
    start, end = st.slider("Custom range", min_value=lo, max_value=hi, value=(lo, hi))
else:
    start, end = lo, hi
st.caption(f"showing **{start} → {end}**")


def clip(df):
    if df is None:
        return None
    out = df[(df["date"].dt.date >= start) & (df["date"].dt.date <= end)]
    return out if not out.empty else None


c = clip(counts)
th = clip(theme_sent)
tsent = clip(tick_sent)
sig_frames = [s for s in (clip(signals), clip(signals_t)) if s is not None]
if c is None:
    st.warning("No rows in this timeframe."); st.stop()

# ---------------- theme series (trailing z computed on FULL history,
# so window changes never distort the baselines - live-parity rule) -------
lookup = build_ticker_to_themes()
_tc = counts.copy()
_tc["themes"] = _tc["ticker"].map(lambda t: lookup.get(t, []))
theme_daily = (_tc.explode("themes").dropna(subset=["themes"])
               .groupby(["date", "themes"])["mention_count"].sum().reset_index()
               .rename(columns={"themes": "theme"}))
all_days = pd.date_range(counts["date"].min(), counts["date"].max(), freq="D")
tw = (theme_daily.pivot_table(index="date", columns="theme", values="mention_count")
      .reindex(all_days).fillna(0))
tw = tw.loc[:, tw.mean() >= 3]
r7 = tw.rolling(7, min_periods=1).sum()
mu = r7.rolling(84, min_periods=28).mean()
sd = r7.rolling(84, min_periods=28).std()
att_z_full = ((r7 - mu) / sd.replace(0, pd.NA)).astype(float)
att_z = att_z_full.loc[str(start):str(end)]

# ---------------- KPI row ----------------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Ticker mentions", f"{int(c['mention_count'].sum()):,}")
hot = att_z.iloc[-1].dropna().sort_values(ascending=False) if len(att_z) else pd.Series(dtype=float)
k2.metric("Hottest theme now", hot.index[0] if len(hot) else "-",
          f"{hot.iloc[0]:+.1f}z" if len(hot) else None)
if tsent is not None:
    mkt = (tsent["net_bullish"] * tsent["n_posts"]).sum() / tsent["n_posts"].sum()
    k3.metric("Crowd lean", f"{mkt:+.2f}", help="post-weighted net-bullish; "
              "retail skews bullish - watch changes, not the level")
else:
    k3.metric("Crowd lean", "run nb 06")
n_sig = sum(len(s) for s in sig_frames)
k4.metric("Model decisions", n_sig)

st.divider()

# ---------------- 1) SIGNALS BLOTTER (notebook 09) ----------------
st.subheader("Model decisions — BUY / SELL with reasons")
if not sig_frames:
    st.info("No signals in this window. Run notebook 09 (or widen the timeframe). "
            "Every decision carries a conviction score /5 and a full reason.")
else:
    sig = pd.concat(sig_frames, ignore_index=True).sort_values("date", ascending=False)
    sig["instrument"] = sig.get("etf", pd.Series(dtype=object)).fillna(
        sig.get("trade", pd.Series(dtype=object)))
    b1, b2, b3 = st.columns(3)
    b1.metric("Decisions", len(sig))
    b2.metric("BUY", int((sig["action"] == "BUY").sum()))
    b3.metric("SELL", int((sig["action"] == "SELL").sum()))
    st.dataframe(sig[["signal_date", "action", "instrument", "score", "reason"]],
                 hide_index=True, use_container_width=True)

st.divider()

# ---------------- 2) THEMES TAKING OFF (notebook 03/05 logic) ----------------
st.subheader("Themes taking off — attention vs own baseline")
st.caption("7-day mentions, z-scored against each theme's own trailing 84-day "
           "baseline. Above the dashed line = statistically unusual crowd (K=2).")
top_now = hot.head(6).index.tolist() if len(hot) else list(tw.columns[:6])
if not top_now or att_z.empty:
    st.info("Not enough history in this window for trailing z-scores "
            "(needs ~28 days of baseline). Pick a longer timeframe.")
    zplot = pd.DataFrame(columns=["date", "theme", "z"])
else:
    zplot = (att_z[top_now].reset_index().rename(columns={"index": "date"})
             .melt("date", var_name="theme", value_name="z"))
zchart = (alt.Chart(zplot).mark_line(interpolate="monotone").encode(
    x=alt.X("date:T", title=None),
    y=alt.Y("z:Q", title="attention z"),
    color=alt.Color("theme:N", legend=alt.Legend(orient="bottom")),
    tooltip=["date:T", "theme:N", alt.Tooltip("z:Q", format="+.2f")]))
rule = alt.Chart(pd.DataFrame({"y": [2.0]})).mark_rule(
    strokeDash=[6, 4], color="#DC2626").encode(y="y:Q")
st.altair_chart((zchart + rule).properties(height=320).interactive(),
                use_container_width=True)

# ---------------- 3) CROWD SENTIMENT BY THEME (notebook 07) ----------------
st.subheader("Crowd sentiment by theme — 7-day rolling net bullish")
if th is None:
    st.info("Run notebook 07 to unlock theme sentiment.")
else:
    top_t = (th.groupby("theme")["n_posts"].sum()
             .sort_values(ascending=False).head(6).index.tolist())
    rows = []
    for theme in top_t:
        one = (th[th["theme"] == theme].set_index("date")
               .reindex(pd.date_range(start, end, freq="D")))
        line = one["net_bullish"].where(one["n_posts"].fillna(0) >= 3) \
                                 .rolling(7, min_periods=2).mean()
        rows.append(pd.DataFrame({
            "date": line.index, "net_bullish": line.values,
            "theme": f"{theme} ({THEME_ETFS.get(theme, '?')})"}))
    schart = (alt.Chart(pd.concat(rows)).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("net_bullish:Q", title="net bullish (-1..+1)",
                scale=alt.Scale(domain=[-1, 1])),
        color=alt.Color("theme:N", legend=alt.Legend(orient="bottom")),
        tooltip=["date:T", "theme:N", alt.Tooltip("net_bullish:Q", format="+.2f")]))
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#94A3B8").encode(y="y:Q")
    st.altair_chart((schart + zero).properties(height=320).interactive(),
                    use_container_width=True)

    # ---------------- 4) WEEKLY SENTIMENT HEATMAP (notebook 08) ----------------
    st.subheader("Sentiment heat — theme × week")
    st.caption("Weekly net-bullish share. Green = bulls dominate, red = bears; "
               "blank = under 10 scored posts that week.")
    hm = th.copy()
    hm["week"] = hm["date"].dt.to_period("W").dt.start_time
    hm["w_net"] = hm["net_bullish"] * hm["n_posts"]
    wk = (hm.groupby(["week", "theme"])
          .agg(w=("w_net", "sum"), n=("n_posts", "sum")).reset_index())
    wk = wk[(wk["n"] >= 10) & (wk["theme"].isin(top_t))].copy()
    wk["net"] = wk["w"] / wk["n"]
    heat = alt.Chart(wk).mark_rect(cornerRadius=3).encode(
        x=alt.X("week:T", title=None),
        y=alt.Y("theme:N", title=None),
        color=alt.Color("net:Q", scale=alt.Scale(scheme="redyellowgreen",
                                                 domain=[-0.6, 0.6]),
                        legend=alt.Legend(title="net bullish")),
        tooltip=["week:T", "theme:N", alt.Tooltip("net:Q", format="+.2f")])
    st.altair_chart(heat.properties(height=40 + 32 * len(top_t)),
                    use_container_width=True)

    # ---------------- 5) MOMENTUM MAP - where attention meets mood ----------
    st.subheader("Momentum map — last 5 days vs window baseline")
    st.caption("x = attention momentum (z) · y = sentiment change vs own average "
               "· bubble = posts. Top-right = crowding in with improving mood.")
    rows = []
    for theme in th["theme"].unique():
        one = (th[th["theme"] == theme].set_index("date")
               .reindex(pd.date_range(start, end, freq="D")))
        n = one["n_posts"].fillna(0)
        if n.sum() < 50:
            continue
        std = n.std()
        lz = (n.iloc[-5:].mean() - n.mean()) / std if std and std > 0 else 0.0
        s5, sb = one["net_bullish"].iloc[-5:].mean(), one["net_bullish"].mean()
        if pd.isna(s5) or pd.isna(sb):
            continue
        rows.append({"theme": f"{theme} ({THEME_ETFS.get(theme, '?')})",
                     "posts": int(n.sum()), "momentum_z": round(float(lz), 2),
                     "sent_change": round(float(s5 - sb), 3),
                     "lean_5d": round(float(s5), 2)})
    mm = pd.DataFrame(rows)
    if len(mm):
        base = alt.Chart(mm).encode(
            x=alt.X("momentum_z:Q", title="mention momentum (z)"),
            y=alt.Y("sent_change:Q", title="sentiment change"),
            tooltip=["theme", "posts", "momentum_z", "sent_change", "lean_5d"])
        pts = base.mark_circle(opacity=.6, stroke="#0F172A", strokeWidth=.5).encode(
            size=alt.Size("posts:Q", legend=None),
            color=alt.Color("lean_5d:Q", scale=alt.Scale(scheme="redyellowgreen",
                                                         domain=[-1, 1]),
                            legend=alt.Legend(title="5d lean")))
        lbl = base.mark_text(dy=-12, fontSize=11).encode(text="theme:N")
        st.altair_chart((pts + lbl).properties(height=420).interactive(),
                        use_container_width=True)

# ---------------- extras, tucked away ----------------
with st.expander("More: attention movers & Reddit vs X split"):
    left, right = st.columns(2)
    with left:
        st.markdown("**Top mentioned tickers**")
        st.dataframe(c.groupby("ticker")["mention_count"].sum()
                     .sort_values(ascending=False).head(12).rename("mentions")
                     .reset_index(), hide_index=True, use_container_width=True)
    with right:
        st.markdown("**Fastest risers (5d velocity z)**")
        wide_t = (c.pivot_table(index="date", columns="ticker",
                                values="mention_count", aggfunc="sum")
                  .asfreq("D").fillna(0))
        velo = wide_t.rolling(7, min_periods=1).sum().diff()
        vz = ((velo - velo.mean()) / velo.std().replace(0, pd.NA)).iloc[-5:].mean()
        st.dataframe(vz.dropna().sort_values(ascending=False).head(12)
                     .round(2).rename("velocity_z").reset_index(),
                     hide_index=True, use_container_width=True)
    bs = clip(by_source)
    if bs is not None and bs["source"].nunique() > 1:
        split = bs.groupby(["date", "source"])["mention_count"].sum().reset_index()
        st.altair_chart(alt.Chart(split).mark_line().encode(
            x="date:T", y="mention_count:Q", color="source:N",
            tooltip=["date:T", "source:N", "mention_count:Q"]).interactive(),
            use_container_width=True)

# ---------------- sidebar: pipeline controls ----------------
st.sidebar.header("Pipeline")
st.sidebar.caption("run_daily.py: fetch live sources → merge → notebooks "
                   "01-09 → snapshot signals. 5-15 min with new data; close "
                   "Jupyter kernels first if a merge is due.")
if st.sidebar.button("🔄 Run daily pipeline now"):
    import subprocess
    with st.spinner("Running the full pipeline - keep this tab open..."):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "run_daily.py")],
                           cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 0:
        st.sidebar.success("Done - reloading fresh data.")
        st.cache_data.clear()
        st.rerun()
    else:
        st.sidebar.error("Pipeline failed - tail of output:")
        st.sidebar.code((r.stdout + "\n" + r.stderr)[-1500:])

st.caption("Sources: notebooks 02 (counts), 06/07 (sentiment), 09 (signals). "
           "Nothing here is synthetic - re-run the pipeline to refresh.")

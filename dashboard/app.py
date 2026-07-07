"""
Retail Flow Tracker - investor dashboard (served by optimised/)
===============================================================
A THIN display layer: all loading lives in optimised.data, all maths in
optimised.metrics, all chart specs in optimised.charts - the same code a
notebook or a future API can import. The dashboard only arranges panels,
handles the timeframe, and shows freshness.

Run from the project root:   streamlit run dashboard/app.py
Charts (chosen for an investor, from notebooks 05/06/07/08/09):
  signals blotter · themes taking off (05) · theme sentiment (07) ·
  conviction z + weekly heat (08) · momentum map · top tickers (06)
"""

import datetime
import os
import sys

import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from optimised import charts, data, metrics                      # noqa: E402
from src.themes import THEME_ETFS                                # noqa: E402

st.set_page_config(page_title="Retail Flow Tracker", page_icon="📡", layout="wide")

# ---- glassy professional light theme ----
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
.status-chip { display:inline-block; padding:2px 12px; border-radius:999px;
  font-size:.8rem; font-weight:600; margin-right:8px; }
.live  { background:#DCFCE7; color:#166534; border:1px solid #86EFAC; }
.hist  { background:#FEF3C7; color:#92400E; border:1px solid #FCD34D; }
.muted { background:#E2E8F0; color:#334155; }
</style>""", unsafe_allow_html=True)


# ---- cached loaders (mtime in the key -> auto-refresh after pipeline runs) ----
@st.cache_data
def _load(name, mtime, date_col="date"):
    return data.load(name, date_col)


def get(name, date_col="date"):
    m = data.mtime(name)
    return None if m is None else _load(name, m, date_col)


counts = get("counts")
theme_sent = get("theme_sent")
tick_sent = get("tick_sent")
signals = get("signals", "signal_date")
signals_t = get("signals_tickers", "signal_date")

st.title("📡 Retail Flow Tracker")
if counts is None:
    st.error("No data yet - run notebooks 01 and 02 (or run_daily.py) first.")
    st.stop()

# ---- status strip: LIVE or not, and when refreshed ----
data_max = counts["date"].max().date()
days_stale = (datetime.date.today() - data_max).days
newest = max(m for m in (data.mtime(k) for k in data.PATHS) if m is not None)
refreshed = datetime.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
log_dir = os.path.join(ROOT, "logs")
last_run = (sorted(os.listdir(log_dir))[-1].replace("run_", "").replace(".log", "")
            if os.path.isdir(log_dir) and os.listdir(log_dir) else "never")
chip = ('<span class="status-chip live">● LIVE - data through today</span>'
        if days_stale <= 2 else
        f'<span class="status-chip hist">◆ HISTORICAL - data ends {data_max} '
        f'({days_stale} days ago)</span>')
st.markdown(chip + f'<span class="status-chip muted">files refreshed {refreshed}</span>'
            + f'<span class="status-chip muted">last pipeline run: {last_run}</span>',
            unsafe_allow_html=True)

# ---- timeframe pills ----
lo, hi = counts["date"].min().date(), data_max
preset = st.radio("Timeframe",
                  ["Latest 30d", "Latest 90d", "Latest 12m", "All data", "Custom"],
                  horizontal=True, index=1,
                  help="'Latest' anchors to the newest data - with the daily "
                       "pipeline scheduled, that IS live/now.")
spans = {"Latest 30d": 30, "Latest 90d": 90, "Latest 12m": 365}
if preset in spans:
    start, end = max(lo, hi - pd.Timedelta(days=spans[preset])), hi
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


# ---- computed frames: maths on FULL history (correct trailing baselines),
#      window applied only to what is DISPLAYED ----
@st.cache_data
def compute(mtime_counts, mtime_sent):
    tw = data.theme_mentions_wide(counts)
    att_z = metrics.trailing_z(tw)
    if theme_sent is not None:
        n, s = data.theme_sent_wide(theme_sent)
        conv_z = metrics.conviction_z(n, s)
        rolled = metrics.rolled_sentiment(theme_sent)
    else:
        conv_z = rolled = None
    return att_z, conv_z, rolled


att_z_full, conv_z_full, rolled_full = compute(data.mtime("counts"),
                                               data.mtime("theme_sent"))
att_z = att_z_full.loc[str(start):str(end)]
c = clip(counts)
th = clip(theme_sent)
tsent = clip(tick_sent)
sig_frames = [s for s in (clip(signals), clip(signals_t)) if s is not None]

hot = (att_z.iloc[-1].dropna().sort_values(ascending=False)
       if len(att_z) else pd.Series(dtype=float))
top_now = hot.head(6).index.tolist()

# ---- KPI row ----
k1, k2, k3, k4 = st.columns(4)
k1.metric("Ticker mentions", f"{int(c['mention_count'].sum()):,}" if c is not None else "0")
k2.metric("Hottest theme now", hot.index[0] if len(hot) else "-",
          f"{hot.iloc[0]:+.1f}z" if len(hot) else None)
if tsent is not None:
    mkt = (tsent["net_bullish"] * tsent["n_posts"]).sum() / tsent["n_posts"].sum()
    k3.metric("Crowd lean", f"{mkt:+.2f}",
              help="post-weighted net-bullish; retail skews bullish - watch changes")
else:
    k3.metric("Crowd lean", "run nb 06")
k4.metric("Model decisions", sum(len(s) for s in sig_frames))

st.divider()

# ---- 1) SIGNALS BLOTTER (10) ----
st.subheader("Model decisions — BUY / SELL with reasons")
if not sig_frames:
    st.info("No signals in this window - run notebook 10 or widen the timeframe.")
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

# ---- 2) THEMES TAKING OFF (05) ----
st.subheader("Themes taking off — attention vs own baseline")
st.caption("7-day mentions z-scored against each theme's own trailing 84-day "
           "baseline; dashed line = take-off threshold (K=2).")
if not top_now or att_z.empty:
    st.info("Not enough history for trailing z (needs ~28 days) - widen the window.")
else:
    st.altair_chart(charts.takeoff_chart(att_z, top_now), use_container_width=True)

# ---- 3) THEME SENTIMENT (07) ----
st.subheader("Crowd sentiment by theme — 7-day rolling net bullish")
if rolled_full is None or th is None:
    st.info("Run notebooks 06+07 to unlock sentiment panels.")
else:
    top_t = (th.groupby("theme")["n_posts"].sum()
             .sort_values(ascending=False).head(6).index.tolist())
    rolled = rolled_full.loc[str(start):str(end)]
    st.altair_chart(charts.sentiment_lines(rolled, top_t, THEME_ETFS),
                    use_container_width=True)

    # ---- 4) RETAIL CONVICTION (08): z lines + weekly heat ----
    st.subheader("Retail conviction — crowd size × direction (notebook 08)")
    st.caption("Bull pressure (posts × net-bullish) z-scored vs own history; "
               "±2 dashed = the notebook-09 entry/exit thresholds.")
    conv_z = conv_z_full.loc[str(start):str(end)]
    conv_themes = [t for t in top_t if t in conv_z.columns]
    if conv_themes:
        st.altair_chart(charts.conviction_lines(conv_z, conv_themes),
                        use_container_width=True)
    st.subheader("Sentiment heat — theme × week")
    wk = metrics.weekly_net(th, top_t)
    if len(wk):
        st.altair_chart(charts.conviction_heat(wk), use_container_width=True)

    # ---- 5) MOMENTUM MAP (06/07) ----
    st.subheader("Momentum map — last 5 days vs window baseline")
    st.caption("x = attention momentum · y = sentiment CHANGE vs own average "
               "· bubble = posts. Top-right = crowding in with improving mood.")
    n_w, s_w = data.theme_sent_wide(th)
    mm = metrics.momentum_stats(n_w, s_w)
    if len(mm):
        st.altair_chart(charts.momentum_map(mm, THEME_ETFS), use_container_width=True)

# ---- extras ----
with st.expander("More: top tickers (mentions · velocity · sentiment)"):
    st.caption("velocity_z = day-over-day change of the EWMA of mentions "
               "(derivative of the smoothed line, not raw counts), z-scored.")
    if c is not None:
        st.dataframe(metrics.top_tickers(c, tsent), hide_index=True,
                     use_container_width=True)

# ---- sidebar: pipeline ----
st.sidebar.header("Pipeline")
st.sidebar.caption("run_daily.py: fetch → merge → notebooks → snapshot. "
                   "5-15 min with new data; close Jupyter first if a merge is due.")
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

st.caption("Analytics served by the optimised/ package (vectorised, cached); "
           "heavy steps run in the pipeline. Nothing here is synthetic.")

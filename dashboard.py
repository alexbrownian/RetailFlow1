# dashboard.py
# ============
# Interactive Streamlit dashboard over the RetailFlow data (v2 - Plotly).
# Every chart is hoverable/zoomable, every section leads with a RANKED table
# so the TOP item is unmistakable, and the Trade Desk shows live, dated
# suggestions (entry -> 20-day exit) ranked by most recent.
#
#     pip install streamlit plotly
#     python -m streamlit run dashboard.py

from __future__ import annotations

import os
import subprocess
import sys

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
P = os.path.join(ROOT, "data", "processed")
PRICES_PATH = os.path.join(ROOT, "data", "prices", "prices.parquet")

from src.themes import THEME_ETFS, THEME_ETF_FALLBACKS  # noqa: E402

st.set_page_config(page_title="RetailFlow", layout="wide",
                   initial_sidebar_state="expanded")

# same knobs as the notebooks
WINDOW = 7          # rolling window for smoothed shares
DERIV_SMOOTH = 5    # smoothing of the day-to-day change
MIN_TOTAL = 30      # mask days with fewer total posts
CROSS_AT = 1.5      # conviction crossing level
MIN_GAP = 10        # days between counted crossings
HOLD_DAYS = 20      # holding period for every suggestion

GREEN, RED, PURPLE, BLUE, GRAY = ("#2ca02c", "#d62728", "#9467bd",
                                  "#1f77b4", "#444444")


# ---------------------------------------------------------------------------
# data loading (cached; invalidates when the file on disk changes)
# ---------------------------------------------------------------------------
def _mtime(path):
    return os.path.getmtime(path) if os.path.exists(path) else 0


@st.cache_data(show_spinner=False)
def _read(path, mtime):
    df = pd.read_parquet(path)
    for col in ("date", "action_date", "signal_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df


def load(name, folder=P):
    path = os.path.join(folder, name)
    return _read(path, _mtime(path)) if os.path.exists(path) else None


def clip(df, col, lo, hi):
    out = df[df[col] >= lo]
    return out if hi is None else out[out[col] <= hi]


def price_series(prices, symbol, lo, hi):
    one = prices[prices["symbol"] == symbol].sort_values("date")
    s = one.set_index("date")["px_last"]
    if not s.empty:
        s = s.asfreq("D").ffill()
    s = s[s.index >= lo]
    return s if hi is None else s[s.index <= hi]


def resolve_anchor(theme, priced):
    candidates = ([THEME_ETFS[theme]] if THEME_ETFS.get(theme) else [])
    candidates += THEME_ETF_FALLBACKS.get(theme, [])
    for sym in candidates:
        if sym in priced:
            return sym
    return None


def norm_share(counts, entity_col, name, lo, hi):
    c = clip(counts, "date", lo, hi)
    day_totals = c.groupby("date")["mention_count"].sum()
    m = (c[c[entity_col] == name].sort_values("date")
         .set_index("date")["mention_count"].asfreq("D").fillna(0))
    if m.empty:
        return m
    totals = day_totals.reindex(m.index).fillna(0).astype("float64")
    share = (m.astype("float64") / totals.where(totals > 0)) * 100
    share[totals < MIN_TOTAL] = float("nan")
    return share.rolling(WINDOW, min_periods=1).mean()


def spaced(idx, gap):
    out = []
    for d in idx:
        if not out or (d - out[-1]).days >= gap:
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# interactive chart builders (Plotly - hover shows the numbers)
# ---------------------------------------------------------------------------
def _base_fig(title):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.update_layout(title=title, height=380, hovermode="x unified",
                      margin=dict(l=10, r=10, t=45, b=10),
                      legend=dict(orientation="h", y=1.12))
    return fig


def fig_series_vs_price(series, series_name, series_color, px, symbol, title):
    fig = _base_fig(title)
    fig.add_trace(go.Scatter(x=series.index, y=series.values,
                             name=series_name, line=dict(color=series_color)),
                  secondary_y=False)
    if px is not None and not px.empty:
        fig.add_trace(go.Scatter(x=px.index, y=px.values,
                                 name=f"{symbol} price",
                                 line=dict(color=GRAY, width=1.5)),
                      secondary_y=True)
    fig.update_yaxes(title_text=series_name, secondary_y=False)
    fig.update_yaxes(title_text="price (USD)", secondary_y=True)
    return fig


def fig_conviction(cz, px, theme, symbol):
    fig = _base_fig(f"{theme} conviction vs {symbol or 'no priced anchor'}")
    fig.add_trace(go.Scatter(x=cz.index, y=cz.values, name="conviction_z",
                             line=dict(color=PURPLE)), secondary_y=False)
    fig.add_hline(y=CROSS_AT, line_dash="dot", line_color=GREEN, opacity=0.6)
    fig.add_hline(y=-CROSS_AT, line_dash="dot", line_color=RED, opacity=0.6)
    if px is not None and not px.empty:
        fig.add_trace(go.Scatter(x=px.index, y=px.values, name=f"{symbol} price",
                                 line=dict(color=GRAY, width=1.5)),
                      secondary_y=True)
        up = spaced(cz[(cz > CROSS_AT) & (cz.shift(1) <= CROSS_AT)].index, MIN_GAP)
        dn = spaced(cz[(cz < -CROSS_AT) & (cz.shift(1) >= -CROSS_AT)].index, MIN_GAP)
        for dates, sym_mk, col, nm in [(up, "triangle-up", GREEN, "bullish crossing"),
                                       (dn, "triangle-down", RED, "bearish crossing")]:
            pts = [(d, px.asof(d)) for d in dates
                   if px.index.min() <= d <= px.index.max()]
            if pts:
                fig.add_trace(go.Scatter(
                    x=[p[0] for p in pts], y=[p[1] for p in pts], name=nm,
                    mode="markers",
                    marker=dict(symbol=sym_mk, size=13, color=col,
                                line=dict(color="black", width=1))),
                    secondary_y=True)
    fig.update_yaxes(title_text="conviction z", secondary_y=False)
    fig.update_yaxes(title_text="price (USD)", secondary_y=True)
    return fig


def fig_signals(px_line, sig_theme, theme, symbol):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=px_line.index, y=px_line.values,
                             name=f"{symbol} price",
                             line=dict(color=GRAY, width=1.5)))
    for side, mk, col in [("BUY", "triangle-up", GREEN),
                          ("SELL", "triangle-down", RED)]:
        rows = sig_theme[sig_theme["action"] == side]
        pts, texts = [], []
        for _, r in rows.iterrows():
            d = r["action_date"]
            if px_line.empty:
                continue
            i = px_line.index.get_indexer([d], method="nearest")[0]
            pts.append((px_line.index[i], px_line.iloc[i]))
            texts.append(f"{side} {d.date()}<br>score {r.get('score', '?')}/5"
                         f"<br>conv z {r.get('conv_z', float('nan')):+.2f}")
        if pts:
            fig.add_trace(go.Scatter(
                x=[p[0] for p in pts], y=[p[1] for p in pts], name=side,
                mode="markers", hovertext=texts, hoverinfo="text",
                marker=dict(symbol=mk, size=14, color=col,
                            line=dict(color="black", width=1))))
    fig.update_layout(title=f"{theme} ({symbol}): BUY/SELL signals",
                      height=380, hovermode="closest",
                      margin=dict(l=10, r=10, t=45, b=10),
                      legend=dict(orientation="h", y=1.12))
    return fig


# ---------------------------------------------------------------------------
# analytics shared with the notebooks
# ---------------------------------------------------------------------------
def certainty_table(sig):
    cert = sig.copy()
    cert["strength"] = cert["conv_z"].abs().clip(upper=3)
    age = (cert["action_date"].max() - cert["action_date"]).dt.days
    cert["recency"] = (1 - age / 90).clip(lower=0)
    cert["certainty"] = cert["score"] + cert["strength"] + cert["recency"]
    return cert.sort_values("certainty", ascending=False)


def trade_desk(sig, prices, priced, today):
    """One row per signal, MOST RECENT FIRST: entry price/date, the dated
    20-day exit, live status and P&L so far (signed - always 'money made')."""
    rows = []
    for _, r in sig.sort_values("action_date", ascending=False).iterrows():
        etf = r.get("etf")
        entry_d = r["action_date"]
        exit_d = entry_d + pd.Timedelta(days=HOLD_DAYS)
        row = {"signal date": entry_d.date(), "action": r["action"],
               "theme": r.get("theme", ""), "ETF": etf,
               "exit by": exit_d.date(),
               "status": "OPEN" if exit_d > today else "closed",
               "days left": max((exit_d - today).days, 0),
               "score": f"{r.get('score', '?')}/5",
               "conv z": round(float(r.get("conv_z", float("nan"))), 2)}
        if etf in priced:
            px = price_series(prices, etf, entry_d - pd.Timedelta(days=5), None)
            p0 = px.asof(entry_d) if not px.empty else float("nan")
            mark_d = min(exit_d, today)
            p1 = px.asof(mark_d) if not px.empty else float("nan")
            if pd.notna(p0) and pd.notna(p1) and p0:
                sign = 1 if r["action"] == "BUY" else -1
                row["entry px"] = round(float(p0), 2)
                row["P&L so far %"] = round(sign * (p1 / p0 - 1) * 100, 2)
        rows.append(row)
    return pd.DataFrame(rows)


def scorecard(sig, prices, priced, lo):
    trades = []
    for _, row in sig.iterrows():
        etf = row.get("etf")
        if etf not in priced:
            continue
        px = price_series(prices, etf, lo, None)
        p0 = px.asof(row["action_date"]) if not px.empty else float("nan")
        p1 = (px.asof(row["action_date"] + pd.Timedelta(days=HOLD_DAYS))
              if not px.empty else float("nan"))
        if pd.isna(p0) or pd.isna(p1) or p0 == 0:
            continue
        sign = 1 if row["action"] == "BUY" else -1
        trades.append({"side": row["action"], "ret": sign * (p1 / p0 - 1) * 100})
    out = []
    for name, g in {"ALL (buy+sell)": trades,
                    "BUY only": [t for t in trades if t["side"] == "BUY"],
                    "SELL only": [t for t in trades if t["side"] == "SELL"]}.items():
        r = pd.Series([t["ret"] for t in g])
        if len(r) < 2:
            out.append({"strategy": name, "trades": len(r)})
            continue
        sharpe = r.mean() / r.std() if r.std() > 0 else float("nan")
        out.append({"strategy": name, "trades": len(r),
                    "avg/trade %": round(r.mean(), 2),
                    "hit rate %": round((r > 0).mean() * 100),
                    "total P&L %": round(r.sum(), 1),
                    "sharpe/trade": round(sharpe, 2),
                    "annualised": round(sharpe * (252 / HOLD_DAYS) ** 0.5, 2)})
    return pd.DataFrame(out)


def ranked(df, by, ascending=False):
    """Add a 1-based 'rank' column - the TOP row is always rank 1."""
    out = df.sort_values(by, ascending=ascending).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out


# ---------------------------------------------------------------------------
# sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("RetailFlow")

theme_counts = load("daily_theme_counts.parquet")
conv = load("daily_theme_conviction.parquet")
sig_file = load("trade_signals.parquet")
prices = _read(PRICES_PATH, _mtime(PRICES_PATH)) if os.path.exists(PRICES_PATH) else None
priced = set(prices["symbol"]) if prices is not None else set()

if theme_counts is None:
    st.error("No aggregate data - run update_data.py first.")
    st.stop()

data_max = theme_counts["date"].max()
today = pd.Timestamp.today().normalize()
lo = pd.Timestamp(st.sidebar.date_input(
    "window start", (data_max - pd.Timedelta(days=365)).date()))
live_mode = st.sidebar.checkbox("LIVE (to newest data)", value=True)
hi = None if live_mode else pd.Timestamp(
    st.sidebar.date_input("window end", data_max.date()))
how_many = st.sidebar.slider("items per section", 3, 15, 6)

st.sidebar.divider()
st.sidebar.subheader("Run the pipeline")


def _stream(script, args, log_area, lines, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen([sys.executable, script] + args, cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            env=env)
    for line in proc.stdout:
        lines.append(line.rstrip())
        log_area.code("\n".join(lines[-25:]))
    proc.wait()
    return proc.returncode


def run_pipeline(steps, label):
    box = st.sidebar.status(f"running {label}...", expanded=True)
    log_area = box.empty()
    lines = []
    for script, args, env_extra in steps:
        lines.append(f"===== {script} {' '.join(args)} =====")
        if _stream(script, args, log_area, lines, env_extra) != 0:
            box.update(label=f"{label} FAILED in {script}", state="error")
            return
    box.update(label=f"{label} finished", state="complete")
    _read.clear()
    st.rerun()


start_s = lo.strftime("%Y-%m-%d")
end_s = "" if hi is None else hi.strftime("%Y-%m-%d")
win_env = {"PIPELINE_START_DATE": start_s, "PIPELINE_END_DATE": end_s}

if st.sidebar.button("run LIVE pull now"):
    run_pipeline([("update_data.py", [], None)], "LIVE pull")
if st.sidebar.button("rebuild THIS window (prices + views)"):
    run_pipeline([("pull_bloomberg_prices.py", [], win_env),
                  ("update_data.py", ["--start", start_s, "--end", end_s], None)],
                 f"window rebuild {start_s} -> {end_s or 'LIVE'}")
if st.sidebar.button("run FULL historical rebuild"):
    run_pipeline([("update_data.py", ["--full"], None)], "FULL rebuild")

if prices is None:
    st.sidebar.warning("prices.parquet missing - run pull_bloomberg_prices.py")

# ---------------------------------------------------------------------------
# tabs
# ---------------------------------------------------------------------------
t_desk, t_top, t_emerging, t_conv, t_hist = st.tabs(
    ["🟢 Trade desk (live)", "Top trends", "Emerging trends",
     "Conviction", "Historical checker"])

tc = clip(theme_counts, "date", lo, hi)

# ---- TRADE DESK: dated live suggestions, most recent first ----
with t_desk:
    st.subheader("Model suggestions - most recent first, 20-day holds")
    if sig_file is None or not len(sig_file):
        st.info("no signals on file - run the pipeline")
    else:
        sig_w = clip(sig_file, "action_date", lo, hi)
        if not len(sig_w):
            st.info("no signals in this window")
        else:
            desk = trade_desk(sig_w.head(200) if len(sig_w) > 200 else sig_w,
                              prices, priced, today)
            desk.insert(0, "rank", range(1, len(desk) + 1))
            open_n = int((desk["status"] == "OPEN").sum())
            c1, c2, c3 = st.columns(3)
            c1.metric("open positions", open_n)
            c2.metric("signals in window", len(sig_w))
            newest = sig_w["action_date"].max()
            c3.metric("latest signal", str(newest.date()))
            st.dataframe(desk, use_container_width=True, hide_index=True,
                         height=420)
            st.markdown(f"**Strategy scorecard ({HOLD_DAYS}d hold, signed P&L)**")
            if prices is not None:
                st.dataframe(scorecard(sig_w, prices, priced, lo),
                             use_container_width=False, hide_index=True)
            st.markdown("**Certainty ranking (score + |conv z| + recency)**")
            cert = certainty_table(sig_w)
            show = ["action_date", "action", "theme", "etf", "score",
                    "conv_z", "certainty"]
            st.dataframe(ranked(cert[[c for c in show if c in cert.columns]]
                                .head(15), "certainty"),
                         use_container_width=True, hide_index=True)
            for theme in cert["theme"].drop_duplicates().head(how_many):
                symbol = resolve_anchor(theme, priced)
                if prices is None or symbol is None:
                    continue
                px_line = price_series(prices, symbol, lo, hi)
                if px_line.empty:
                    continue
                st.plotly_chart(fig_signals(px_line,
                                            sig_w[sig_w["theme"] == theme],
                                            theme, symbol),
                                use_container_width=True)

# ---- TOP TRENDS ----
with t_top:
    st.subheader("Most-mentioned themes (rank 1 = top trending)")
    top = (tc.groupby("theme")["mention_count"].sum()
           .rename("total mentions").reset_index())
    top_r = ranked(top, "total mentions").head(how_many)
    st.dataframe(top_r, use_container_width=False, hide_index=True)
    for i, theme in enumerate(top_r["theme"], 1):
        symbol = resolve_anchor(theme, priced)
        share = norm_share(theme_counts, "theme", theme, lo, hi)
        px = (price_series(prices, symbol, lo, hi)
              if prices is not None and symbol else None)
        st.plotly_chart(fig_series_vs_price(
            share, "share of posts (%, 7d avg)", BLUE, px, symbol,
            f"#{i}  {theme}  vs  {symbol or 'no priced anchor'}"),
            use_container_width=True)

# ---- EMERGING TRENDS ----
with t_emerging:
    st.subheader("Emerging = fastest-GROWING tradeable themes (rank 1 = hottest)")
    st.caption("Only themes with an approved instrument are ranked. "
               "'Growing' = average change in share-of-conversation over the "
               "last 7 days - positive means the crowd is arriving.")
    movers = []
    for theme in tc["theme"].unique():
        if theme not in THEME_ETFS:          # tradeable themes only
            continue
        share = norm_share(theme_counts, "theme", theme, lo, hi)
        chg = share.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()
        tail = chg.dropna().tail(7)
        if len(tail):
            movers.append({"theme": theme,
                           "avg change last 7d (pp)": round(tail.mean(), 3)})
    mv = ranked(pd.DataFrame(movers),
                "avg change last 7d (pp)").head(how_many)
    st.dataframe(mv, use_container_width=False, hide_index=True)
    for i, theme in enumerate(mv["theme"], 1):
        symbol = resolve_anchor(theme, priced)
        share = norm_share(theme_counts, "theme", theme, lo, hi)
        chg = share.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()
        px = (price_series(prices, symbol, lo, hi)
              if prices is not None and symbol else None)
        st.plotly_chart(fig_series_vs_price(
            chg, "chatter change (pp, smoothed)", GREEN, px, symbol,
            f"#{i}  {theme}: change in chatter  vs  {symbol or '-'}"),
            use_container_width=True)

# ---- CONVICTION ----
with t_conv:
    st.subheader("Conviction (rank 1 = most abnormal crowd, last 30d)")
    if conv is None:
        st.info("no conviction data - run the pipeline")
    else:
        cv = clip(conv, "date", lo, hi)
        cv = cv[cv["theme"].isin(THEME_ETFS)]     # tradeable universe only
        recent = (cv[cv["date"] >= cv["date"].max() - pd.Timedelta(days=30)]
                  .groupby("theme")["conviction_z"].mean().reset_index())
        recent["abs"] = recent["conviction_z"].abs()
        rk = (ranked(recent, "abs").drop(columns="abs")
              .rename(columns={"conviction_z": "avg conviction z (30d)"})
              .round(2).head(how_many))
        st.dataframe(rk, use_container_width=False, hide_index=True)
        for i, theme in enumerate(rk["theme"], 1):
            cz = (cv[cv["theme"] == theme].sort_values("date")
                  .set_index("date")["conviction_z"].asfreq("D").ffill())
            symbol = resolve_anchor(theme, priced)
            px = (price_series(prices, symbol, lo, hi)
                  if prices is not None and symbol else None)
            fig = fig_conviction(cz, px, f"#{i}  {theme}", symbol)
            st.plotly_chart(fig, use_container_width=True)

# ---- HISTORICAL CHECKER ----
with t_hist:
    st.subheader("Historical lookback: any window, any theme")
    c1, c2 = st.columns(2)
    h_lo = pd.Timestamp(c1.date_input(
        "from", (data_max - pd.Timedelta(days=730)).date(), key="h_lo"))
    h_hi = pd.Timestamp(c2.date_input(
        "to", (data_max - pd.Timedelta(days=365)).date(), key="h_hi"))
    h_theme = st.selectbox("theme", sorted(tc["theme"].unique()))
    symbol = resolve_anchor(h_theme, priced)
    px = (price_series(prices, symbol, h_lo, h_hi)
          if prices is not None and symbol else None)
    if conv is not None:
        cz = (clip(conv, "date", h_lo, h_hi)
              .query("theme == @h_theme").sort_values("date")
              .set_index("date")["conviction_z"].asfreq("D").ffill())
        if len(cz):
            st.plotly_chart(fig_conviction(cz, px, h_theme, symbol),
                            use_container_width=True)
    if sig_file is not None:
        s_h = clip(sig_file, "action_date", h_lo, h_hi)
        s_ht = s_h[s_h["theme"] == h_theme]
        if len(s_ht) and px is not None and not px.empty:
            st.plotly_chart(fig_signals(px, s_ht, h_theme, symbol),
                            use_container_width=True)
        if prices is not None and len(s_h):
            st.markdown(f"**Scorecard, whole window ({len(s_h)} signals)**")
            st.dataframe(scorecard(s_h, prices, priced, h_lo),
                         use_container_width=False, hide_index=True)

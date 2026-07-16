# dashboard.py
# ============
# Streamlit dashboard over the RetailFlow data. Charts are the SAME designs
# as the notebooks (11-17) - same normalisation, same conviction maths, same
# certainty ranking - just interactive.
#
#     pip install streamlit
#     streamlit run dashboard.py
#
# Works on either machine: everything reads ABSTRACTED_DATA / data/processed
# and prices.parquet. The RUN buttons in the sidebar launch update_data.py
# and stream its progress into the page.

from __future__ import annotations

import os
import subprocess
import sys

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
P = os.path.join(ROOT, "data", "processed")
PRICES_PATH = os.path.join(ROOT, "data", "prices", "prices.parquet")

from src.themes import THEME_ETFS, THEME_ETF_FALLBACKS  # noqa: E402

st.set_page_config(page_title="RetailFlow", layout="wide")

# same knobs as the notebooks
WINDOW = 7          # rolling window for smoothed shares (nb 11-13)
DERIV_SMOOTH = 5    # smoothing of the day-to-day change (nb 12/13)
MIN_TOTAL = 30      # mask days with fewer total posts (nb 11-13)
CROSS_AT = 1.5      # conviction crossing level (nb 14/17)
MIN_GAP = 10        # days between counted crossings (nb 14/17)
CLUSTER_DAYS = 10   # same-side signals this close = one cluster (nb 15/16)
CONV_MIN = 2        # cluster size that counts as HIGH CONVICTION (nb 15/16)
HOLD_DAYS = 20      # scorecard holding period (nb 17)


# ---------------------------------------------------------------------------
# data loading (cached; invalidates when the file on disk changes)
# ---------------------------------------------------------------------------
def _mtime(path):
    return os.path.getmtime(path) if os.path.exists(path) else 0


@st.cache_data(show_spinner=False)
def _read(path, mtime):
    df = pd.read_parquet(path)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    for col in ("action_date", "signal_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df


def load(name, folder=P):
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        return None
    return _read(path, _mtime(path))


def clip(df, col, lo, hi):
    out = df[df[col] >= lo]
    return out if hi is None else out[out[col] <= hi]


def price_series(prices, symbol, lo, hi):
    """Daily close, forward-filled continuous, clipped - same as notebooks."""
    one = prices[prices["symbol"] == symbol].sort_values("date")
    s = one.set_index("date")["px_last"]
    if not s.empty:
        s = s.asfreq("D").ffill()
    s = s[s.index >= lo]
    return s if hi is None else s[s.index <= hi]


def resolve_anchor(theme, priced):
    """Anchor ETF for a theme, walking the fallbacks (same as notebooks)."""
    candidates = []
    if THEME_ETFS.get(theme):
        candidates.append(THEME_ETFS[theme])
    candidates += THEME_ETF_FALLBACKS.get(theme, [])
    for sym in candidates:
        if sym in priced:
            return sym
    return None


def norm_share(counts, entity_col, name, lo, hi):
    """Smoothed share-of-conversation series for one theme/ticker -
    identical to the notebooks' NORMALISE branch."""
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
# chart builders - each mirrors one notebook's chart
# ---------------------------------------------------------------------------
def chart_mentions_vs_price(share, px, name, symbol):
    """nb 11/13 style: smoothed share (left) vs price (right)."""
    fig, ax1 = plt.subplots(figsize=(11, 4))
    ax1.plot(share.index, share.values, color="tab:blue", linewidth=1.5,
             label=f"{name} share of conversation (7d avg)")
    ax1.set_ylabel("share of posts (%)", color="tab:blue")
    ax1.tick_params(axis="y", labelcolor="tab:blue")
    ax2 = ax1.twinx()
    if px is not None and not px.empty:
        ax2.plot(px.index, px.values, color="tab:red", linewidth=1.4,
                 label=f"{symbol} price")
        ax2.set_ylabel("price (USD)", color="tab:red")
        ax2.tick_params(axis="y", labelcolor="tab:red")
    ax1.set_title(f"{name}  vs  {symbol or 'no priced anchor'}")
    ax1.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def chart_change_vs_price(share, px, name, symbol):
    """nb 12/13 style: smoothed CHANGE in chatter (left) vs price (right)."""
    chg = share.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()
    fig, ax1 = plt.subplots(figsize=(11, 4))
    ax1.axhline(0, color="black", linewidth=0.6)
    ax1.plot(chg.index, chg.values, color="tab:green", linewidth=1.5,
             label="chatter change (smoothed)")
    ax1.set_ylabel("chatter change (pp)", color="tab:green")
    ax1.tick_params(axis="y", labelcolor="tab:green")
    ax2 = ax1.twinx()
    if px is not None and not px.empty:
        ax2.plot(px.index, px.values, color="tab:red", linewidth=1.4)
        ax2.set_ylabel("price (USD)", color="tab:red")
        ax2.tick_params(axis="y", labelcolor="tab:red")
    ax1.set_title(f"{name}: change in chatter  vs  {symbol or 'no priced anchor'}")
    ax1.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def chart_conviction(cz, px, theme, symbol):
    """nb 14/17 style: conviction_z with +/-CROSS_AT crossings on price."""
    fig, ax1 = plt.subplots(figsize=(11, 4))
    ax1.axhline(0, color="black", linewidth=0.6)
    ax1.axhline(CROSS_AT, color="tab:green", linestyle=":", linewidth=0.8)
    ax1.axhline(-CROSS_AT, color="tab:red", linestyle=":", linewidth=0.8)
    ax1.plot(cz.index, cz.values, color="tab:purple", linewidth=1.3,
             label="conviction_z")
    ax1.set_ylabel("conviction z", color="tab:purple")
    ax1.tick_params(axis="y", labelcolor="tab:purple")
    ax2 = ax1.twinx()
    if px is not None and not px.empty:
        ax2.plot(px.index, px.values, color="black", linewidth=1.3)
        ax2.set_ylabel("price (USD)")
        up = spaced(cz[(cz > CROSS_AT) & (cz.shift(1) <= CROSS_AT)].index, MIN_GAP)
        down = spaced(cz[(cz < -CROSS_AT) & (cz.shift(1) >= -CROSS_AT)].index, MIN_GAP)
        for d in up:
            if px.index.min() <= d <= px.index.max():
                ax2.scatter([d], [px.asof(d)], marker="^", s=90,
                            color="tab:green", edgecolors="black",
                            linewidths=0.8, zorder=5)
        for d in down:
            if px.index.min() <= d <= px.index.max():
                ax2.scatter([d], [px.asof(d)], marker="v", s=90,
                            color="tab:red", edgecolors="black",
                            linewidths=0.8, zorder=5)
    ax1.set_title(f"{theme} conviction  vs  {symbol or 'no priced anchor'}")
    ax1.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def cluster_signals(one_df):
    """nb 15/16: same-side signals within CLUSTER_DAYS = one cluster."""
    clusters = []
    for side in ("BUY", "SELL"):
        dates = list(one_df[one_df["action"] == side]
                     .sort_values("action_date")["action_date"])
        if not dates:
            continue
        cur = [dates[0]]
        for dt in dates[1:]:
            if (dt - cur[-1]).days <= CLUSTER_DAYS:
                cur.append(dt)
            else:
                clusters.append({"side": side, "dates": cur, "n": len(cur)})
                cur = [dt]
        clusters.append({"side": side, "dates": cur, "n": len(cur)})
    return sorted(clusters, key=lambda c: c["dates"][0])


def chart_signals(px_line, sig_theme, theme, symbol):
    """nb 16 style: price with clustered BUY/SELL triangles SNAPPED to the
    line (big triangle + xN = high-conviction cluster)."""
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(px_line.index, px_line.values, color="black", linewidth=1.3,
            label=f"{symbol} price")
    for cl in cluster_signals(sig_theme):
        mid = cl["dates"][len(cl["dates"]) // 2]
        if px_line.empty:
            continue
        i = px_line.index.get_indexer([mid], method="nearest")[0]
        mid, price_at = px_line.index[i], px_line.iloc[i]
        color = "tab:green" if cl["side"] == "BUY" else "tab:red"
        marker = "^" if cl["side"] == "BUY" else "v"
        if cl["n"] >= CONV_MIN:
            size = min(200 + 180 * (cl["n"] - CONV_MIN + 1), 900)
            ax.scatter([mid], [price_at], marker=marker, s=size, color=color,
                       edgecolors="black", linewidths=1.5, zorder=6)
            ax.annotate(f"x{cl['n']}", (mid, price_at),
                        textcoords="offset points",
                        xytext=(0, 14 if cl["side"] == "BUY" else -20),
                        fontsize=10, fontweight="bold", ha="center", color=color)
        else:
            ax.scatter([mid], [price_at], marker=marker, s=70, color=color,
                       alpha=0.55, zorder=5)
    ax.set_title(f"{theme} ({symbol}): BUY/SELL signals")
    ax.set_ylabel("price (USD)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def certainty_table(sig):
    """nb 16's certainty ranking: score + capped |conv z| + recency."""
    cert = sig.copy()
    cert["strength"] = cert["conv_z"].abs().clip(upper=3)
    age = (cert["action_date"].max() - cert["action_date"]).dt.days
    cert["recency"] = (1 - age / 90).clip(lower=0)
    cert["certainty"] = cert["score"] + cert["strength"] + cert["recency"]
    return cert.sort_values("certainty", ascending=False)


def scorecard(sig, prices, priced, lo, hi):
    """nb 17's 20-day-hold strategy table (signed trade P&L)."""
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
    rows = []
    groups = {"ALL (buy+sell)": trades,
              "BUY only": [t for t in trades if t["side"] == "BUY"],
              "SELL only": [t for t in trades if t["side"] == "SELL"]}
    for name, g in groups.items():
        r = pd.Series([t["ret"] for t in g])
        if len(r) < 2:
            rows.append({"strategy": name, "trades": len(r)})
            continue
        sharpe = r.mean() / r.std() if r.std() > 0 else float("nan")
        rows.append({"strategy": name, "trades": len(r),
                     "avg/trade %": round(r.mean(), 2),
                     "hit rate %": round((r > 0).mean() * 100),
                     "total P&L %": round(r.sum(), 1),
                     "sharpe/trade": round(sharpe, 2),
                     "annualised": round(sharpe * (252 / HOLD_DAYS) ** 0.5, 2)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# sidebar: window + RUN buttons
# ---------------------------------------------------------------------------
st.sidebar.title("RetailFlow")

theme_counts = load("daily_theme_counts.parquet")
ticker_counts = load("daily_ticker_counts.parquet")
conv = load("daily_theme_conviction.parquet")
sig_file = load("trade_signals.parquet")
prices = _read(PRICES_PATH, _mtime(PRICES_PATH)) if os.path.exists(PRICES_PATH) else None
priced = set(prices["symbol"]) if prices is not None else set()

if theme_counts is None:
    st.error("No aggregate data found - run update_data.py first "
             "(on the work laptop: git pull, then a live run).")
    st.stop()

data_max = theme_counts["date"].max()
default_lo = data_max - pd.Timedelta(days=365)
lo = pd.Timestamp(st.sidebar.date_input("window start", default_lo.date()))
live_mode = st.sidebar.checkbox("LIVE (to newest data)", value=True)
hi = None if live_mode else pd.Timestamp(
    st.sidebar.date_input("window end", data_max.date()))
how_many = st.sidebar.slider("how many themes per section", 3, 15, 6)

st.sidebar.divider()
st.sidebar.subheader("Run the pipeline")
st.sidebar.caption("Streams update_data.py output below. LIVE = fetch + fold "
                   "+ signals. FULL = rebuild history (external machine only).")


def _stream(script, args, log_area, lines, env_extra=None):
    """Run one script, streaming its output into the sidebar log.
    Returns the exit code."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen([sys.executable, script] + args,
                            cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env)
    for line in proc.stdout:
        lines.append(line.rstrip())
        log_area.code("\n".join(lines[-25:]))     # rolling tail of progress
    proc.wait()
    return proc.returncode


def run_pipeline(steps, label):
    """steps: list of (script, args, env_extra). Stops at the first failure."""
    box = st.sidebar.status(f"running {label}...", expanded=True)
    log_area = box.empty()
    lines = []
    for script, args, env_extra in steps:
        lines.append(f"===== {script} {' '.join(args)} =====")
        code = _stream(script, args, log_area, lines, env_extra)
        if code != 0:
            box.update(label=f"{label} FAILED in {script} (code {code})",
                       state="error")
            return
    box.update(label=f"{label} finished", state="complete")
    _read.clear()                                  # reload data next rerun
    st.rerun()


start_s = lo.strftime("%Y-%m-%d")
end_s = "" if hi is None else hi.strftime("%Y-%m-%d")
win_env = {"PIPELINE_START_DATE": start_s, "PIPELINE_END_DATE": end_s}

if st.sidebar.button("run LIVE pull now"):
    run_pipeline([("update_data.py", [], None)], "LIVE pull")

if st.sidebar.button("rebuild THIS window (prices + views)"):
    # like running update_data with a changed timeframe: pull Bloomberg
    # prices FOR THE SIDEBAR WINDOW first (needs the Terminal running),
    # then the backtest pass re-renders every view for that window.
    run_pipeline([("pull_bloomberg_prices.py", [], win_env),
                  ("update_data.py", ["--start", start_s, "--end", end_s], None)],
                 f"window rebuild {start_s} -> {end_s or 'LIVE'}")

if st.sidebar.button("run FULL historical rebuild"):
    run_pipeline([("update_data.py", ["--full"], None)], "FULL rebuild")

if prices is None:
    st.sidebar.warning("prices.parquet missing - price overlays will be "
                       "empty. Run pull_bloomberg_prices.py.")

# ---------------------------------------------------------------------------
# tabs
# ---------------------------------------------------------------------------
t1, t2, t3, t4, t5, t6 = st.tabs(
    ["Top trends", "Emerging trends", "Live week", "Conviction",
     "Trading suggestions", "Historical checker"])

tc = clip(theme_counts, "date", lo, hi)

# ---- 1. TOP TRENDS: most mentions, vs price (nb 11/13 style) ----
with t1:
    st.subheader("Most-mentioned themes in the window")
    top = (tc.groupby("theme")["mention_count"].sum()
           .sort_values(ascending=False).head(how_many))
    st.dataframe(top.rename("total mentions"), use_container_width=False)
    for theme in top.index:
        symbol = resolve_anchor(theme, priced)
        share = norm_share(theme_counts, "theme", theme, lo, hi)
        px = price_series(prices, symbol, lo, hi) if (prices is not None and symbol) else None
        st.pyplot(chart_mentions_vs_price(share, px, theme, symbol))
        plt.close("all")

# ---- 2. EMERGING TRENDS: mentions CHANGING, vs price (nb 12/13 style) ----
with t2:
    st.subheader("Fastest-changing themes (chatter derivative)")
    movers = {}
    for theme in tc["theme"].unique():
        share = norm_share(theme_counts, "theme", theme, lo, hi)
        chg = share.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()
        tail = chg.dropna().tail(7)
        if len(tail):
            movers[theme] = tail.mean()
    ranked = sorted(movers.items(), key=lambda kv: abs(kv[1]), reverse=True)[:how_many]
    st.dataframe(pd.DataFrame(ranked, columns=["theme", "avg chatter change, last 7d (pp)"])
                 .round(3), use_container_width=False)
    for theme, _ in ranked:
        symbol = resolve_anchor(theme, priced)
        share = norm_share(theme_counts, "theme", theme, lo, hi)
        px = price_series(prices, symbol, lo, hi) if (prices is not None and symbol) else None
        st.pyplot(chart_change_vs_price(share, px, theme, symbol))
        plt.close("all")

# ---- 3. LIVE WEEK: 1-week lookback trading table ----
with t3:
    st.subheader("Signals from the last 7 days (the trading lookback)")
    if sig_file is None or not len(sig_file):
        st.info("no trade_signals.parquet - run the pipeline first")
    else:
        newest = sig_file["action_date"].max()
        week = sig_file[sig_file["action_date"] >= newest - pd.Timedelta(days=7)]
        if len(week):
            cols = ["action_date", "action", "theme", "etf", "score",
                    "att_z", "conv_z", "sent_5d_chg", "reason"]
            st.dataframe(week.sort_values("action_date", ascending=False)[
                [c for c in cols if c in week.columns]].round(2),
                use_container_width=True)
        else:
            st.info(f"no signals in the week ending {newest.date()} - "
                    "the strict thresholds mean silence is normal")
        st.caption(f"signal file newest action date: {newest.date()} | "
                   f"aggregates newest: {data_max.date()}")

# ---- 4. CONVICTION: momentum trend (sentiment + attention) ----
with t4:
    st.subheader("Conviction (bull pressure z) vs anchor price")
    if conv is None:
        st.info("no daily_theme_conviction.parquet - run notebook 09 / the pipeline")
    else:
        cv = clip(conv, "date", lo, hi)
        latest = (cv[cv["date"] >= cv["date"].max() - pd.Timedelta(days=30)]
                  .groupby("theme")["conviction_z"].mean().abs()
                  .sort_values(ascending=False).head(how_many))
        for theme in latest.index:
            cz = (cv[cv["theme"] == theme].sort_values("date")
                  .set_index("date")["conviction_z"].asfreq("D").ffill())
            symbol = resolve_anchor(theme, priced)
            px = price_series(prices, symbol, lo, hi) if (prices is not None and symbol) else None
            st.pyplot(chart_conviction(cz, px, theme, symbol))
            plt.close("all")

# ---- 5. TRADING SUGGESTIONS: nb 16 + live performance ----
with t5:
    st.subheader("Certainty-ranked suggestions (nb 16) + performance (nb 17)")
    if sig_file is None or not len(sig_file):
        st.info("no signals on file")
    else:
        sig_w = clip(sig_file, "action_date", lo, hi)
        if not len(sig_w):
            st.info("no signals in this window")
        else:
            cert = certainty_table(sig_w)
            show = ["action_date", "action", "theme", "etf", "score",
                    "conv_z", "certainty"]
            st.markdown("**Ranked by certainty = score + |conv z| (cap 3) + recency**")
            st.dataframe(cert[[c for c in show if c in cert.columns]]
                         .head(15).round(2), use_container_width=True)
            st.markdown(f"**Strategy scorecard - {HOLD_DAYS}d hold, signed P&L**")
            if prices is not None:
                st.dataframe(scorecard(sig_w, prices, priced, lo, hi),
                             use_container_width=False)
            best_themes = (cert.groupby("theme")["certainty"].max()
                           .sort_values(ascending=False).head(how_many).index)
            for theme in best_themes:
                symbol = resolve_anchor(theme, priced)
                if prices is None or symbol is None:
                    continue
                px_line = price_series(prices, symbol, lo, hi)
                if px_line.empty:
                    continue
                st.pyplot(chart_signals(px_line,
                                        sig_w[sig_w["theme"] == theme],
                                        theme, symbol))
                plt.close("all")

# ---- 6. HISTORICAL CHECKER: pick any past window ----
with t6:
    st.subheader("Historical lookback: conviction + signals for any window")
    c1, c2 = st.columns(2)
    h_lo = pd.Timestamp(c1.date_input("from", (data_max - pd.Timedelta(days=730)).date(),
                                      key="hist_lo"))
    h_hi = pd.Timestamp(c2.date_input("to", (data_max - pd.Timedelta(days=365)).date(),
                                      key="hist_hi"))
    h_theme = st.selectbox("theme", sorted(tc["theme"].unique()))
    symbol = resolve_anchor(h_theme, priced)
    px = price_series(prices, symbol, h_lo, h_hi) if (prices is not None and symbol) else None
    if conv is not None:
        cz = (clip(conv, "date", h_lo, h_hi)
              .query("theme == @h_theme").sort_values("date")
              .set_index("date")["conviction_z"].asfreq("D").ffill())
        if len(cz):
            st.pyplot(chart_conviction(cz, px, h_theme, symbol))
            plt.close("all")
        else:
            st.info("no conviction data for this theme/window")
    if sig_file is not None:
        s_h = clip(sig_file, "action_date", h_lo, h_hi)
        s_ht = s_h[s_h["theme"] == h_theme]
        if len(s_ht) and px is not None and not px.empty:
            st.pyplot(chart_signals(px, s_ht, h_theme, symbol))
            plt.close("all")
        st.markdown(f"**Scorecard for the whole window ({len(s_h)} signals)**")
        if prices is not None and len(s_h):
            st.dataframe(scorecard(s_h, prices, priced, h_lo, h_hi),
                         use_container_width=False)

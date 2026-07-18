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

ACCENT = "#e8845c"                       # coral - the dashboard accent
GREEN, RED, PURPLE, BLUE, GRAY = ("#3fb950", "#f85149", "#b58bd8",
                                  ACCENT, "#9aa0a6")

# GIC logo: the official file wins if present, otherwise an inline SVG
# recreation of the mark (navy bars + orbit + wordmark, on a white chip so
# the navy stays readable on the dark theme)
GIC_LOGO_PNG = os.path.join(ROOT, "assets", "gic_logo.png")
GIC_LOGO_SVG = """
<svg width="118" height="46" viewBox="0 0 260 100" xmlns="http://www.w3.org/2000/svg">
  <rect width="260" height="100" rx="8" fill="#ffffff"/>
  <g fill="#12275e">
    <rect x="30" y="26" width="8" height="48"/>
    <rect x="44" y="14" width="8" height="72"/>
    <rect x="58" y="6"  width="8" height="88"/>
    <rect x="72" y="14" width="8" height="72"/>
    <rect x="86" y="26" width="8" height="48"/>
  </g>
  <ellipse cx="62" cy="50" rx="46" ry="9" fill="none"
           stroke="#12275e" stroke-width="6"/>
  <text x="118" y="72" font-family="Arial, Helvetica, sans-serif"
        font-size="58" font-weight="bold" fill="#12275e">GIC</text>
</svg>"""

# Animated GIC loader: the five bars rise ONE BY ONE (staggered delays keep
# their phase every loop), then the orbit line draws itself through the
# middle, everything fades, and the cycle repeats.
GIC_LOADER_HTML = """
<div style="display:flex;align-items:center;gap:14px;padding:6px 0;">
<svg width="72" height="72" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg">
  <style>
    .gb { fill:#e8845c; transform-box:fill-box; transform-origin:50% 100%;
          transform:scaleY(0);
          animation:gicbar 2.6s cubic-bezier(.4,0,.2,1) infinite; }
    .g1 { animation-delay:0s;   } .g2 { animation-delay:.14s; }
    .g3 { animation-delay:.28s; } .g4 { animation-delay:.42s; }
    .g5 { animation-delay:.56s; }
    .gorb { fill:none; stroke:#e8845c; stroke-width:5;
            stroke-dasharray:155; stroke-dashoffset:155;
            animation:gicorb 2.6s ease-in-out infinite; }
    @keyframes gicbar {
      0%   { transform:scaleY(0); opacity:1; }
      18%  { transform:scaleY(1); opacity:1; }
      82%  { transform:scaleY(1); opacity:1; }
      95%  { transform:scaleY(1); opacity:0; }
      100% { transform:scaleY(0); opacity:0; }
    }
    @keyframes gicorb {
      0%, 30% { stroke-dashoffset:155; opacity:1; }
      60%     { stroke-dashoffset:0;   opacity:1; }
      82%     { stroke-dashoffset:0;   opacity:1; }
      95%     { stroke-dashoffset:0;   opacity:0; }
      100%    { stroke-dashoffset:155; opacity:0; }
    }
  </style>
  <rect class="gb g1" x="22" y="38" width="8" height="46"/>
  <rect class="gb g2" x="36" y="26" width="8" height="70"/>
  <rect class="gb g3" x="50" y="16" width="8" height="90"/>
  <rect class="gb g4" x="64" y="26" width="8" height="70"/>
  <rect class="gb g5" x="78" y="38" width="8" height="46"/>
  <ellipse class="gorb" cx="54" cy="61" rx="45" ry="9"/>
</svg>
<span style="color:#9aa0a6;">working...</span>
</div>"""

TERMINAL_CSS = """
<style>
html, body, [data-testid="stAppViewContainer"] * {
    font-family: 'SFMono-Regular', Consolas, 'Cascadia Mono',
                 'Courier New', monospace !important;
}
/* EXCEPTION: Streamlit's icons are a FONT (Material Symbols) - without
   this rule the monospace override above turns every icon into its
   literal ligature text, e.g. 'arrow_right' on expanders */
span[data-testid="stIconMaterial"],
[data-testid="stExpanderToggleIcon"],
.material-icons, [class*="material-symbols"] {
    font-family: 'Material Symbols Rounded', 'Material Icons' !important;
}
[data-testid="stMetricValue"] { color: #e8845c; }
[data-testid="stMetricLabel"] { color: #9aa0a6; }
h1, h2, h3 { letter-spacing: 0.02em; }
div[data-testid="stExpander"] { border: 1px solid #24262b; border-radius: 8px; }
.rf-title { color: #e8845c; font-size: 1.7rem; font-weight: 700; }
.rf-dot { color: #3fb950; }
.rf-sub { color: #9aa0a6; font-size: 0.85rem; }
</style>"""

CONV_DEF = """**Conviction = how convinced the crowd is, measured against how
convinced that crowd usually is.**

Built in three steps:

1. **Bull pressure** - every scored post is a vote: clearly positive =
   bullish vote, clearly negative = bearish vote. Bull pressure for a day is
   bullish minus bearish votes, so it grows with both how one-sided the
   crowd is *and* how many people showed up.
2. **7-day rolling sum** - one loud afternoon is not conviction; a sustained
   week of lean is.
3. **Trailing z-score** - that sum is compared against the same theme's own
   PRECEDING 84 days: `(today - its own recent mean) / its own recent spread`.

So **conviction z = +2** reads: *this theme is two standard deviations more
bullish-active than is normal for this theme lately.* A permanently loud
theme sits near 0; a quiet theme that suddenly gains a devoted bullish crowd
spikes - and that abnormality, not raw loudness, is where the trade is.
Crossings of the +/-1.5 lines are marked on the charts with triangles."""

SIGNAL_DEF = """### The philosophy: fewer trades, more conviction

A signal only fires when **momentum and sentiment agree** - neither alone
is enough. Everything is measured against each theme's OWN trailing 84-day
baseline (never whole-window statistics), so a signal on day *t* uses only
information available on day *t* - what you see in a backtest is exactly
what the live run would have produced.

### What makes a BUY

All three must hold on the same day:

1. **A momentum trigger CROSSES up.** Attention z or conviction z crosses
   above **+K (2.5)** - crossing means *yesterday <= K, today > K*, so one
   surge produces exactly one trade, not a signal every day the surge
   lasts. K=2.5 means only the top ~1% most abnormal days for that theme
   even qualify.
2. **Sentiment agrees.** The 5-day change of the net-bullish share is
   POSITIVE - the mood is improving vs its own recent past, not just loud.
3. **Score >= 4 of 5** (see the checklist below).

### What makes a SELL

The mirror image, with a deliberately harder bar (retail skews bullish, so
bearish evidence must be stronger): a bearish trigger (conviction z crosses
below **-K**, or the *crowded-top* divergence activates - attention above K
while the mood deteriorates, the classic distribution/top pattern), plus a
NEGATIVE 5-day sentiment change, plus a sell score >= **4 of 5**.

### The score: one point per independent check

| # | BUY check | SELL check |
|---|---|---|
| 1 | attention z > K (crowd unusually large) | same |
| 2 | 5d sentiment change > 0 (mood improving) | 5d change < 0 (deteriorating) |
| 3 | conviction z > K (crowd large AND bullish) | conviction z < -K |
| 4 | crowded-top flag NOT active | crowded-top flag ACTIVE |
| 5 | Reddit AND X mentions both rising (where X has coverage) | same |

**Score 5/5** = every independent line of evidence agreed;
**4/5** = the minimum that trades. The `reason` column of every signal
spells out exactly which checks fired with their actual numbers - no
signal is a black box.

### Glossary of the columns

- **attention z (att_z)** - how unusually LARGE the crowd is: 7-day rolling
  mentions vs the theme's own trailing 84-day normal. Says nothing about
  direction, only size.
- **conviction z (conv_z)** - how unusually BULLISH-ACTIVE the crowd is:
  bullish-minus-bearish post votes, 7-day rolling, same trailing baseline.
  Size x direction in one number.
- **sentiment 5d change (sent_5d_chg)** - is the *mood itself* improving or
  deteriorating: the 5-day change in the share of bullish posts. The
  earliest, twitchiest ingredient.
- **crowded top** - attention > K while sentiment deteriorates: everyone is
  watching but enthusiasm is fading, i.e. whoever wanted to buy already
  has. Counts FOR a sell, AGAINST a buy.
- **signal date vs action date** - the signal is computed on day *t* from
  data through day *t*; the order is stamped for the NEXT day (no
  look-ahead).
- **exit by** - every suggestion is a **20-day hold** (chosen from the
  horizon analysis: the edge peaks and plateaus around 3-4 weeks).
- **cooldown (21d)** - once a theme signals, the SAME side is suppressed
  for 21 days: one episode, one trade.
- **certainty** - the desk's ranking metric: score (breadth of evidence)
  + |conviction z| capped at 3 (strength) + a recency bonus fading over
  90 days (a live edge beats an old one)."""


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
def _dark(fig):
    """Terminal look for every chart: dark template, transparent card."""
    fig.update_layout(template="plotly_dark",
                      paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Consolas, monospace", size=12))
    fig.update_xaxes(gridcolor="#24262b")
    fig.update_yaxes(gridcolor="#24262b")
    return fig


def _axes_fidelity(fig):
    """More x-axis points + readable labels on every chart, and keep the
    LEGEND BELOW the plot so it can never collide with the title."""
    fig.update_xaxes(nticks=24, tickformat="%d %b %y", tickangle=-40)
    return fig


def _base_fig(title):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.update_layout(title=dict(text=title, y=0.97, x=0.01),
                      height=430, hovermode="x unified",
                      margin=dict(l=10, r=10, t=55, b=20),
                      legend=dict(orientation="h", yanchor="top", y=-0.28))
    return _axes_fidelity(_dark(fig))


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
    fig.update_layout(title=dict(text=f"{theme} ({symbol}): BUY/SELL signals",
                                 y=0.97, x=0.01),
                      height=430, hovermode="closest",
                      margin=dict(l=10, r=10, t=55, b=20),
                      legend=dict(orientation="h", yanchor="top", y=-0.28))
    return _axes_fidelity(_dark(fig))


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
st.markdown(TERMINAL_CSS, unsafe_allow_html=True)

# ---- header: title + status left, GIC logo top corner ----
h_left, h_right = st.columns([5, 1])
with h_left:
    st.markdown(
        '<div><span class="rf-dot">●</span> '
        '<span class="rf-title">retailflow</span></div>'
        '<div class="rf-sub">retail attention &amp; trading signals - '
        'real-time monitoring dashboard</div>'
        f'<div class="rf-sub">last update: '
        f'{pd.Timestamp.now():%d/%m/%Y, %H:%M:%S}</div>',
        unsafe_allow_html=True)
with h_right:
    if os.path.exists(GIC_LOGO_PNG):
        st.image(GIC_LOGO_PNG, width=118)
    else:
        st.markdown(GIC_LOGO_SVG, unsafe_allow_html=True)
st.divider()

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
    loader = box.empty()
    loader.markdown(GIC_LOADER_HTML, unsafe_allow_html=True)   # GIC animation
    log_area = box.empty()
    lines = []
    for script, args, env_extra in steps:
        lines.append(f"===== {script} {' '.join(args)} =====")
        if _stream(script, args, log_area, lines, env_extra) != 0:
            loader.empty()
            box.update(label=f"{label} FAILED in {script}", state="error")
            return
    loader.empty()
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
# ---- topline metric strip (reference-design style) ----
_m1, _m2, _m3, _m4, _m5 = st.columns(5)
_sig_n = len(clip(sig_file, "action_date", lo, hi)) if sig_file is not None else 0
_open_n = 0
if sig_file is not None and len(sig_file):
    _open_n = int((sig_file["action_date"]
                   > today - pd.Timedelta(days=HOLD_DAYS)).sum())
_m1.metric("signals in window", _sig_n)
_m2.metric("open positions", _open_n)
_m3.metric("themes tracked", int(theme_counts["theme"].nunique()))
_m4.metric("data through", str(data_max.date()))
_m5.metric("priced symbols", len(priced))

# ---- TICKER LOOKUP: one instrument, its suggestions + the reasons ----
with st.expander("🔎 ticker lookup - all suggestions & reasons for one "
                 "instrument", expanded=False):
    if sig_file is None or not len(sig_file):
        st.info("no signals on file yet")
    else:
        lk_opts = sorted(sig_file["etf"].dropna().unique())
        lk = st.selectbox("instrument", lk_opts, key="lookup_etf")
        lk_rows = (sig_file[sig_file["etf"] == lk]
                   .sort_values("action_date", ascending=False))
        if not len(lk_rows):
            st.info(f"no signals ever recorded for {lk}")
        else:
            latest = lk_rows.iloc[0]
            st.markdown(
                f"**latest: {latest['action']} {lk} on "
                f"{latest['action_date'].date()}** - theme "
                f"{latest.get('theme', '?')}, score {latest.get('score', '?')}/5, "
                f"conv z {latest.get('conv_z', float('nan')):+.2f}, exit by "
                f"{(latest['action_date'] + pd.Timedelta(days=HOLD_DAYS)).date()}")
            if latest.get("reason"):
                st.markdown(f"why: _{latest['reason']}_")
            lk_show = [c for c in ["action_date", "action", "theme", "score",
                                   "att_z", "conv_z", "sent_5d_chg", "reason"]
                       if c in lk_rows.columns]
            st.dataframe(lk_rows[lk_show].round(2),
                         width="stretch", hide_index=True)

t_desk, t_pulse, t_top, t_emerging, t_conv, t_hist = st.tabs(
    ["🟢 Trade desk (live)", "🤖 AI Pulse (sample)", "Top trends",
     "Emerging trends", "Conviction", "Historical checker"])

tc = clip(theme_counts, "date", lo, hi)


# ---------------------------------------------------------------------------
# AI PULSE (sample) - the future LLM layer. Everything below marked SAMPLE
# is hand-written placeholder text; the plan is an LLM reading the live
# posts at fold time and writing these sections for real.
# ---------------------------------------------------------------------------
PULSE_TALK_SAMPLE = (
    "SAMPLE - The forums are talking about the robotics supply chain above "
    "everything else this week - bearings, actuators and the Japanese "
    "component makers keep surfacing in threads that begin as Nvidia "
    "discussions. Rate-cut speculation is the steady background hum, "
    "earnings positioning threads are multiplying ahead of semis reporting, "
    "and a smaller but persistent conversation about uranium refuses to "
    "die down. Crypto talk is notably absent relative to how loud it "
    "usually is.")

PULSE_RALLY_SAMPLE = [
    {"target": "bearings / robot components",
     "verdict": "clear rallying detected",
     "why": "SAMPLE - A cluster of high-engagement posts is actively "
            "recruiting: repeated 'get in before the institutions' framing, "
            "posts listing the same four component makers in the same "
            "order, and comment sections coordinating around 'the next "
            "NVDA'. The language is evangelical rather than analytical - "
            "posters answer objections with slogans, not numbers.",
     "example": "SAMPLE paraphrase - 'Everyone is watching the robot "
                "makers, nobody is watching who supplies the joints. Load "
                "the suppliers before the street catches on.'"},
    {"target": "a small-cap uranium name",
     "verdict": "early signs, watch",
     "why": "SAMPLE - A handful of near-identical bullish posts appeared "
            "within hours of each other from young accounts, all citing "
            "the same unsourced supply rumour. Engagement is still low - "
            "either an organic story starting or a seeding attempt.",
     "example": "SAMPLE paraphrase - 'Not many people know about this one "
                "yet. The contract news drops next week. You were warned.'"},
    {"target": "meme stocks (GME and friends)",
     "verdict": "no rallying detected",
     "why": "SAMPLE - Mentions exist but the tone is nostalgic, not "
            "mobilising - jokes about past squeezes rather than calls to "
            "action. No coordinated timing, no recruiting language."},
]

PULSE_MARKET_SAMPLE = (
    "SAMPLE - Retail chatter this week is dominated by the semiconductor "
    "complex, with attention rotating out of megacap AI names into the "
    "supply chain (equipment, memory, robotics components). Mood is "
    "cautiously bullish: bullish share is above its 90-day average but "
    "well off the March highs, and the loudest thread topics are "
    "earnings-positioning rather than momentum-chasing - typically a "
    "mid-cycle pattern rather than a top. Bearish energy is concentrated "
    "in rate-sensitive sectors; crypto chatter is quiet relative to its "
    "own history.")

PULSE_SEGMENTS_SAMPLE = {
    "semiconductors (SMH)": "SAMPLE - Overwhelmingly constructive; the "
        "crowd frames dips as entries. Recurring topics: HBM supply, "
        "capex cycles. Dissent is about valuation, not thesis.",
    "rates & bonds (TLT)": "SAMPLE - Split and argumentative. Half the "
        "posts position for cuts, half mock that trade. High sarcasm "
        "share - read sentiment scores with caution here.",
    "meme / squeeze (ARKK)": "SAMPLE - Quiet vs its own history. The "
        "usual suspects get mentions but engagement is low - no active "
        "squeeze narrative this week.",
    "energy (XLE)": "SAMPLE - Sleepy but turning: a small, persistent "
        "uptick in bullish posts citing seasonality. Watch if it "
        "crosses the conviction threshold.",
}

PULSE_IDEAS = """**Other things the LLM layer can extract from the live posts**
(each is a planned segment - the same API call can return all of them):

- **Retail mood gauge (0-100)** - a fear/greed-style dial with a one-line
  justification, comparable day over day.
- **Narrative tracker** - not just *what* is discussed but *why*: "retail
  attributes the semis rally to HBM shortage chatter", with links between
  themes.
- **Catalyst watch** - events the crowd is positioning for (earnings dates,
  product launches, macro prints), ranked by how much chatter they drive.
- **Euphoria / contrarian warnings** - names where the language turns
  uncritical (rockets, 'can't lose', all-in posts) - historically a
  distribution signal; pairs with the crowded-top flag.
- **Divergence detector** - where retail's story disagrees with price
  action ('crowd bullish, price falling') - candidate squeeze/washout
  setups.
- **Sarcasm-adjusted sentiment** - the lexicon reads 'great, another red
  day' as positive; an LLM does not. A daily corrected sentiment for the
  noisiest themes.
- **Representative quotes** - three verbatim posts per hot theme (with
  scores), so the desk can read the raw voice without opening Reddit.
- **Pump/scam radar** - coordinated-promotion patterns on small names,
  flagged before their counts pollute the mention data."""

# ---- TRADE DESK: dated live suggestions, most recent first ----
with t_desk:
    st.subheader("Model suggestions - most recent first, 20-day holds")
    with st.expander("how a BUY/SELL is decided - full definition & glossary"):
        st.markdown(SIGNAL_DEF)
    if sig_file is None or not len(sig_file):
        st.info("no signals on file - run the pipeline")
    else:
        # follow ONE instrument: filters the table, scorecard, ranking and
        # charts below to just that ETF
        etf_opts = (["ALL (every instrument)"]
                    + sorted(x for x in sig_file["etf"].dropna().unique()))
        pick_etf = st.selectbox("follow one ETF (filters everything below)",
                                etf_opts, key="desk_etf")
        sig_w = clip(sig_file, "action_date", lo, hi)
        if pick_etf != "ALL (every instrument)":
            sig_w = sig_w[sig_w["etf"] == pick_etf]
        if not len(sig_w):
            st.info("no signals in this window"
                    + ("" if pick_etf.startswith("ALL")
                       else f" for {pick_etf}"))
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
            st.markdown("**Live trade ledger** - every suggestion, newest "
                        "first, with entry, dated 20-day exit, status and "
                        "P&L so far (the full per-side P&L breakdown lives "
                        "in notebooks 15/16)")
            st.dataframe(desk, width="stretch", hide_index=True,
                         height=420)
            st.markdown(f"**Strategy scorecard ({HOLD_DAYS}d hold, signed P&L)**")
            if prices is not None:
                st.dataframe(scorecard(sig_w, prices, priced, lo),
                             width="content", hide_index=True)
            st.markdown("**Certainty ranking (score + |conv z| + recency)**")
            cert = certainty_table(sig_w)
            show = ["action_date", "action", "theme", "etf", "score",
                    "conv_z", "certainty"]
            st.dataframe(ranked(cert[[c for c in show if c in cert.columns]]
                                .head(15), "certainty"),
                         width="stretch", hide_index=True)
            st.markdown("#### Signal charts - one per theme, ranked by "
                        "certainty (best trade first)")
            st.caption("Each chart shows a theme's anchor ETF price with "
                       "that theme's BUY/SELL triangles in the window. The "
                       "order follows the certainty ranking above; below "
                       "each chart, every trade is explained in words (the "
                       "signal engine's own `reason`).")
            for theme in cert["theme"].drop_duplicates().head(how_many):
                symbol = resolve_anchor(theme, priced)
                if prices is None or symbol is None:
                    continue
                px_line = price_series(prices, symbol, lo, hi)
                if px_line.empty:
                    continue
                th_rows = (sig_w[sig_w["theme"] == theme]
                           .sort_values("action_date", ascending=False))
                st.plotly_chart(fig_signals(px_line, th_rows, theme, symbol),
                                width="stretch")
                # the trades on this chart, explained
                for _, r in th_rows.head(6).iterrows():
                    st.caption(
                        f"• {r['action_date'].date()} **{r['action']}** "
                        f"(score {r.get('score', '?')}/5, conv z "
                        f"{r.get('conv_z', float('nan')):+.2f}) - "
                        f"{r.get('reason', 'no reason recorded')}")

# ---- AI PULSE (sample placeholders for the future LLM layer) ----
with t_pulse:
    st.subheader("AI market pulse - what an LLM will write from the live posts")
    st.warning("PREVIEW: the text sections below are HAND-WRITTEN SAMPLES, "
               "not generated from your data. They show the format the "
               "future LLM layer will fill in at every live pull. The word "
               "cloud IS real when your term-counts file exists.")

    st.markdown("### 1 - What the forums are talking about")
    st.info(PULSE_TALK_SAMPLE)

    st.markdown("### 2 - The market in one paragraph")
    st.info(PULSE_MARKET_SAMPLE)

    st.markdown("### 3 - What retail thinks, segment by segment")
    cols = st.columns(2)
    for i, (seg, txt) in enumerate(PULSE_SEGMENTS_SAMPLE.items()):
        with cols[i % 2]:
            st.markdown(f"**{seg}**")
            st.info(txt)

    st.markdown("### 4 - Rallying watch")
    st.caption("The LLM reads the posts for MOBILISING language - "
               "recruiting, coordinated timing, evangelical tone, "
               "identical talking points from young accounts - and reports "
               "what is being rallied, how convincingly, and why it "
               "concluded that. Verdicts are words, not scores.")
    for r in PULSE_RALLY_SAMPLE:
        icon = ("🔴" if "clear" in r["verdict"]
                else "🟡" if "early" in r["verdict"] else "🟢")
        with st.expander(f"{icon}  {r['target']} - {r['verdict']}"):
            st.markdown(r["why"])
            if r.get("example"):
                st.markdown(f"> {r['example']}")

    with st.expander("planned LLM segments (the full roadmap)"):
        st.markdown(PULSE_IDEAS)
    st.caption("Implementation note: the LLM reads the freshly fetched raw "
               "posts DURING the live fold (before they are abstracted), "
               "writes these sections, and only the finished text is stored "
               "- consistent with the text-free data boundary.")

# ---- TOP TRENDS ----
with t_top:
    st.subheader("Most-mentioned themes (rank 1 = top trending)")
    top = (tc.groupby("theme")["mention_count"].sum()
           .rename("total mentions").reset_index())
    top_r = ranked(top, "total mentions").head(how_many)
    st.dataframe(top_r, width="content", hide_index=True)
    for i, theme in enumerate(top_r["theme"], 1):
        symbol = resolve_anchor(theme, priced)
        share = norm_share(theme_counts, "theme", theme, lo, hi)
        px = (price_series(prices, symbol, lo, hi)
              if prices is not None and symbol else None)
        st.plotly_chart(fig_series_vs_price(
            share, "share of posts (%, 7d avg)", BLUE, px, symbol,
            f"#{i}  {theme}  vs  {symbol or 'no priced anchor'}"),
            width="stretch")

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
    if not movers:
        st.info("no tradeable theme has enough chatter data in this window "
                "to measure growth - widen the window (a theme needs days "
                f"with {MIN_TOTAL}+ total posts and a 7-day run-up)")
    else:
        mv = ranked(pd.DataFrame(movers),
                    "avg change last 7d (pp)").head(how_many)
        st.dataframe(mv, width="content", hide_index=True)
        for i, theme in enumerate(mv["theme"], 1):
            symbol = resolve_anchor(theme, priced)
            share = norm_share(theme_counts, "theme", theme, lo, hi)
            chg = share.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()
            px = (price_series(prices, symbol, lo, hi)
                  if prices is not None and symbol else None)
            st.plotly_chart(fig_series_vs_price(
                chg, "chatter change (pp, smoothed)", GREEN, px, symbol,
                f"#{i}  {theme}: change in chatter  vs  {symbol or '-'}"),
                width="stretch")

# ---- CONVICTION ----
with t_conv:
    st.subheader("Conviction (rank 1 = most abnormal crowd, last 30d)")
    with st.expander("what is conviction? (definition)"):
        st.markdown(CONV_DEF)
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
        st.dataframe(rk, width="content", hide_index=True)
        for i, theme in enumerate(rk["theme"], 1):
            cz = (cv[cv["theme"] == theme].sort_values("date")
                  .set_index("date")["conviction_z"].asfreq("D").ffill())
            symbol = resolve_anchor(theme, priced)
            px = (price_series(prices, symbol, lo, hi)
                  if prices is not None and symbol else None)
            fig = fig_conviction(cz, px, f"#{i}  {theme}", symbol)
            st.plotly_chart(fig, width="stretch")

# ---- HISTORICAL CHECKER ----
with t_hist:
    st.subheader("Historical lookback: any window, any theme")
    c1, c2 = st.columns(2)
    h_lo = pd.Timestamp(c1.date_input(
        "from", (data_max - pd.Timedelta(days=730)).date(), key="h_lo"))
    h_hi = pd.Timestamp(c2.date_input(
        "to", (data_max - pd.Timedelta(days=365)).date(), key="h_hi"))
    # theme picker shows its anchor ETF right in the label
    labels = {}
    for t in sorted(tc["theme"].unique()):
        a = resolve_anchor(t, priced) or THEME_ETFS.get(t, "no anchor")
        labels[f"{t}  ({a})"] = t
    h_lab = st.selectbox("theme (anchor ETF)", list(labels))
    h_theme = labels[h_lab]
    symbol = resolve_anchor(h_theme, priced)
    px = (price_series(prices, symbol, h_lo, h_hi)
          if prices is not None and symbol else None)

    st.markdown("### 1 - Conviction vs price")
    st.caption("How abnormally bullish-active the crowd was vs its own "
               "trailing normal (see the definition in the Conviction tab). "
               "Triangles = crossings of +/-1.5.")
    if conv is not None:
        cz = (clip(conv, "date", h_lo, h_hi)
              .query("theme == @h_theme").sort_values("date")
              .set_index("date")["conviction_z"].asfreq("D").ffill())
        if len(cz):
            st.plotly_chart(fig_conviction(cz, px, h_theme, symbol),
                            width="stretch")
        else:
            st.info("no conviction data for this theme/window")

    st.markdown("### 2 - Trading signals on price")
    st.caption("The model's actual BUY/SELL calls (all 5 checks, K "
               "threshold, cooldown) placed on the price line.")
    if sig_file is not None:
        s_h = clip(sig_file, "action_date", h_lo, h_hi)
        s_ht = s_h[s_h["theme"] == h_theme]
        if len(s_ht) and px is not None and not px.empty:
            st.plotly_chart(fig_signals(px, s_ht, h_theme, symbol),
                            width="stretch")
        else:
            st.info(f"no signals for {h_theme} in this window")
        if prices is not None and len(s_h):
            st.markdown(f"**Scorecard, whole window ({len(s_h)} signals, "
                        "all themes)**")
            st.dataframe(scorecard(s_h, prices, priced, h_lo),
                         width="content", hide_index=True)

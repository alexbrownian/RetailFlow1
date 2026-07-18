MD_1213 = "## Chatter change → price gradient, and who leads whom\n\nThree views over the SAME names as the charts above:\n\n1. **Scatter** — each dot is one name-day: smoothed chatter change (x) vs the\n   price move over the NEXT `FWD_DAYS` days (y). Forward, not same-day, so a\n   relationship here means chatter *predicts*, not just coincides.\n2. **Deciles** — average forward move per chatter-change bucket. A staircase\n   from red (left) to green (right) = the correlation is real and monotonic.\n3. **Lead/lag** — correlation of today's chatter change with the daily price\n   move k days away, for k = -MAX_LAG..+MAX_LAG. A peak RIGHT of zero means\n   chatter leads price by that many days (the tradeable case); a peak LEFT\n   of zero means price moves first and chatter follows (chasing)."

CODE_12 = '# ==== CHATTER CHANGE -> PRICE GRADIENT + LEAD/LAG (tickers) ====\nGRAD_THRESH = 0.5    # \'positive chatter change\' = smoothed change above this (pp)\nFWD_DAYS    = 5      # price gradient horizon: forward move over the next N days\nMAX_LAG     = 15     # lead/lag scan range in days\n\ncounts_ll = pd.read_parquet(os.path.join(P, \'daily_ticker_counts.parquet\'))\ncounts_ll[\'date\'] = pd.to_datetime(counts_ll[\'date\']); counts_ll = clip_dates(counts_ll, \'date\')\nday_totals_ll = counts_ll.groupby(\'date\')[\'mention_count\'].sum()\nprices_ll = load_prices()\nranked_ll = counts_ll.groupby(\'ticker\')[\'mention_count\'].sum().sort_values(ascending=False)\nnames = [t for t in ranked_ll.index if t in set(prices_ll[\'symbol\'])][:HOW_MANY]\n\npairs, curves = [], {}\nlags = list(range(-MAX_LAG, MAX_LAG + 1))\nfor name in names:\n    m = (counts_ll[counts_ll[\'ticker\'] == name].sort_values(\'date\')\n         .set_index(\'date\')[\'mention_count\'].asfreq(\'D\').fillna(0))\n    if NORMALISE:\n        totals = day_totals_ll.reindex(m.index).fillna(0).astype(\'float64\')\n        m = (m.astype(\'float64\') / totals.where(totals > 0)) * 100\n        m[totals < MIN_TOTAL] = float(\'nan\')\n    px = price_series(prices_ll, name)\n    if px.empty:\n        continue\n    base = m.rolling(WINDOW, min_periods=1).mean() if NORMALISE else m.rolling(WINDOW).sum()\n    chg = base.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()\n    fwd = (px.shift(-FWD_DAYS) / px - 1) * 100\n    both = pd.DataFrame({\'chatter_chg\': chg, \'fwd_ret\': fwd}).dropna()\n    both[\'name\'] = name\n    pairs.append(both)\n    dr = px.pct_change() * 100\n    curves[name] = [chg.corr(dr.shift(-k)) for k in lags]\n\nif pairs:\n    pairs = pd.concat(pairs)\n    r = pairs[\'chatter_chg\'].corr(pairs[\'fwd_ret\'])\n    print(f\'correlation: chatter change vs next-{FWD_DAYS}d price move: \'\n          f\'r = {r:+.3f} over {len(pairs):,} name-days\')\n    for label, grp in [\n            (f\'chatter chg > +{GRAD_THRESH}\', pairs[pairs[\'chatter_chg\'] > GRAD_THRESH]),\n            (f\'|chatter chg| <= {GRAD_THRESH}\', pairs[pairs[\'chatter_chg\'].abs() <= GRAD_THRESH]),\n            (f\'chatter chg < -{GRAD_THRESH}\', pairs[pairs[\'chatter_chg\'] < -GRAD_THRESH])]:\n        if len(grp):\n            pos = (grp[\'fwd_ret\'] > 0).mean() * 100\n            print(f\'  {label:<24} {len(grp):>6,} days | price gradient positive \'\n                  f\'{pos:4.0f}% of the time | avg {grp["fwd_ret"].mean():+.2f}%\')\n\n    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(17, 5))\n\n    pos_m = pairs[\'chatter_chg\'] > GRAD_THRESH\n    neg_m = pairs[\'chatter_chg\'] < -GRAD_THRESH\n    ax1.scatter(pairs.loc[~pos_m & ~neg_m, \'chatter_chg\'],\n                pairs.loc[~pos_m & ~neg_m, \'fwd_ret\'], s=8, alpha=0.2, color=\'gray\')\n    ax1.scatter(pairs.loc[pos_m, \'chatter_chg\'], pairs.loc[pos_m, \'fwd_ret\'],\n                s=8, alpha=0.4, color=\'tab:green\', label=f\'chg > +{GRAD_THRESH}\')\n    ax1.scatter(pairs.loc[neg_m, \'chatter_chg\'], pairs.loc[neg_m, \'fwd_ret\'],\n                s=8, alpha=0.4, color=\'tab:red\', label=f\'chg < -{GRAD_THRESH}\')\n    ax1.axhline(0, color=\'black\', linewidth=0.8); ax1.axvline(0, color=\'black\', linewidth=0.8)\n    ax1.set_xlabel(\'chatter change (smoothed)\'); ax1.set_ylabel(f\'next-{FWD_DAYS}d price move (%)\')\n    ax1.set_title(f\'each dot = one name-day (r = {r:+.3f})\'); ax1.legend(); ax1.grid(True, alpha=0.3)\n\n    bins = pd.qcut(pairs[\'chatter_chg\'], 10, duplicates=\'drop\')\n    summary = pairs.groupby(bins, observed=True)[\'fwd_ret\'].agg([\'mean\', \'count\'])\n    ax2.bar(range(len(summary)), summary[\'mean\'],\n            color=[\'tab:red\' if v < 0 else \'tab:green\' for v in summary[\'mean\']])\n    ax2.set_xticks(range(len(summary)))\n    ax2.set_xticklabels([f\'{iv.left:.2f}\' for iv in summary.index], rotation=45, fontsize=7)\n    ax2.axhline(0, color=\'black\', linewidth=0.8)\n    ax2.set_xlabel(\'chatter-change decile (lower edge)\')\n    ax2.set_ylabel(f\'avg next-{FWD_DAYS}d move (%)\')\n    ax2.set_title(\'a red->green staircase = real signal\'); ax2.grid(True, alpha=0.3)\n\n    mean_curve = pd.DataFrame(curves, index=lags).mean(axis=1)\n    best_lag = int(mean_curve.idxmax()) if mean_curve.notna().any() else 0\n    for nm, c in curves.items():\n        ax3.plot(lags, c, alpha=0.25, linewidth=1)\n    ax3.plot(lags, mean_curve.values, linewidth=2.5, color=\'tab:blue\', label=\'average\')\n    ax3.axvline(0, color=\'black\', linewidth=0.8); ax3.axhline(0, color=\'black\', linewidth=0.8)\n    ax3.axvline(best_lag, color=\'tab:orange\', linestyle=\'--\')\n    ax3.annotate(f\'peak {best_lag:+d}d\', (best_lag, mean_curve.max()),\n                 textcoords=\'offset points\', xytext=(5, 5), color=\'tab:orange\')\n    ax3.set_xlabel(\'lag k (days): k > 0 means chatter TODAY vs price k days LATER\')\n    ax3.set_ylabel(\'correlation\'); ax3.set_title(\'lead/lag: peak right of 0 = chatter leads\')\n    ax3.legend(); ax3.grid(True, alpha=0.3)\n    fig.tight_layout(); plt.show()\n\n    print(f\'\\nlead/lag verdict: correlation peaks at {best_lag:+d} days \'\n          + (\'-> chatter LEADS price (tradeable window)\' if best_lag > 0 else\n         \'-> price moves first or same-day (chatter follows/chases)\'))\nelse:\n    print(\'no priced names with data in this window\')'

CODE_13 = '# ==== CHATTER CHANGE -> PRICE GRADIENT + LEAD/LAG (themes) ====\nGRAD_THRESH = 0.5    # \'positive chatter change\' = smoothed change above this (pp)\nFWD_DAYS    = 5      # price gradient horizon: forward move over the next N days\nMAX_LAG     = 15     # lead/lag scan range in days\n\ncounts_ll = pd.read_parquet(os.path.join(P, \'daily_theme_counts.parquet\'))\ncounts_ll[\'date\'] = pd.to_datetime(counts_ll[\'date\']); counts_ll = clip_dates(counts_ll, \'date\')\nday_totals_ll = counts_ll.groupby(\'date\')[\'mention_count\'].sum()\nprices_ll = load_prices()\nif \'mention_count\' not in counts_ll.columns:\n    counts_ll[\'mention_count\'] = (counts_ll.get(\'keyword_count\', 0)\n                                  + counts_ll.get(\'inferred_count\', 0))\nranked_ll = counts_ll.groupby(\'theme\')[\'mention_count\'].sum().sort_values(ascending=False)\npriced_ll = set(prices_ll[\'symbol\'])\nnames = [t for t in ranked_ll.index if THEME_ETFS.get(t) in priced_ll][:HOW_MANY]\n\npairs, curves = [], {}\nlags = list(range(-MAX_LAG, MAX_LAG + 1))\nfor name in names:\n    m = (counts_ll[counts_ll[\'theme\'] == name].sort_values(\'date\')\n         .set_index(\'date\')[\'mention_count\'].asfreq(\'D\').fillna(0))\n    if NORMALISE:\n        totals = day_totals_ll.reindex(m.index).fillna(0).astype(\'float64\')\n        m = (m.astype(\'float64\') / totals.where(totals > 0)) * 100\n        m[totals < MIN_TOTAL] = float(\'nan\')\n    px = price_series(prices_ll, THEME_ETFS.get(name, \'\'))\n    if px.empty:\n        continue\n    base = m.rolling(WINDOW, min_periods=1).mean() if NORMALISE else m.rolling(WINDOW).sum()\n    chg = base.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()\n    fwd = (px.shift(-FWD_DAYS) / px - 1) * 100\n    both = pd.DataFrame({\'chatter_chg\': chg, \'fwd_ret\': fwd}).dropna()\n    both[\'name\'] = name\n    pairs.append(both)\n    dr = px.pct_change() * 100\n    curves[name] = [chg.corr(dr.shift(-k)) for k in lags]\n\nif pairs:\n    pairs = pd.concat(pairs)\n    r = pairs[\'chatter_chg\'].corr(pairs[\'fwd_ret\'])\n    print(f\'correlation: chatter change vs next-{FWD_DAYS}d price move: \'\n          f\'r = {r:+.3f} over {len(pairs):,} name-days\')\n    for label, grp in [\n            (f\'chatter chg > +{GRAD_THRESH}\', pairs[pairs[\'chatter_chg\'] > GRAD_THRESH]),\n            (f\'|chatter chg| <= {GRAD_THRESH}\', pairs[pairs[\'chatter_chg\'].abs() <= GRAD_THRESH]),\n            (f\'chatter chg < -{GRAD_THRESH}\', pairs[pairs[\'chatter_chg\'] < -GRAD_THRESH])]:\n        if len(grp):\n            pos = (grp[\'fwd_ret\'] > 0).mean() * 100\n            print(f\'  {label:<24} {len(grp):>6,} days | price gradient positive \'\n                  f\'{pos:4.0f}% of the time | avg {grp["fwd_ret"].mean():+.2f}%\')\n\n    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(17, 5))\n\n    pos_m = pairs[\'chatter_chg\'] > GRAD_THRESH\n    neg_m = pairs[\'chatter_chg\'] < -GRAD_THRESH\n    ax1.scatter(pairs.loc[~pos_m & ~neg_m, \'chatter_chg\'],\n                pairs.loc[~pos_m & ~neg_m, \'fwd_ret\'], s=8, alpha=0.2, color=\'gray\')\n    ax1.scatter(pairs.loc[pos_m, \'chatter_chg\'], pairs.loc[pos_m, \'fwd_ret\'],\n                s=8, alpha=0.4, color=\'tab:green\', label=f\'chg > +{GRAD_THRESH}\')\n    ax1.scatter(pairs.loc[neg_m, \'chatter_chg\'], pairs.loc[neg_m, \'fwd_ret\'],\n                s=8, alpha=0.4, color=\'tab:red\', label=f\'chg < -{GRAD_THRESH}\')\n    ax1.axhline(0, color=\'black\', linewidth=0.8); ax1.axvline(0, color=\'black\', linewidth=0.8)\n    ax1.set_xlabel(\'chatter change (smoothed)\'); ax1.set_ylabel(f\'next-{FWD_DAYS}d price move (%)\')\n    ax1.set_title(f\'each dot = one name-day (r = {r:+.3f})\'); ax1.legend(); ax1.grid(True, alpha=0.3)\n\n    bins = pd.qcut(pairs[\'chatter_chg\'], 10, duplicates=\'drop\')\n    summary = pairs.groupby(bins, observed=True)[\'fwd_ret\'].agg([\'mean\', \'count\'])\n    ax2.bar(range(len(summary)), summary[\'mean\'],\n            color=[\'tab:red\' if v < 0 else \'tab:green\' for v in summary[\'mean\']])\n    ax2.set_xticks(range(len(summary)))\n    ax2.set_xticklabels([f\'{iv.left:.2f}\' for iv in summary.index], rotation=45, fontsize=7)\n    ax2.axhline(0, color=\'black\', linewidth=0.8)\n    ax2.set_xlabel(\'chatter-change decile (lower edge)\')\n    ax2.set_ylabel(f\'avg next-{FWD_DAYS}d move (%)\')\n    ax2.set_title(\'a red->green staircase = real signal\'); ax2.grid(True, alpha=0.3)\n\n    mean_curve = pd.DataFrame(curves, index=lags).mean(axis=1)\n    best_lag = int(mean_curve.idxmax()) if mean_curve.notna().any() else 0\n    for nm, c in curves.items():\n        ax3.plot(lags, c, alpha=0.25, linewidth=1)\n    ax3.plot(lags, mean_curve.values, linewidth=2.5, color=\'tab:blue\', label=\'average\')\n    ax3.axvline(0, color=\'black\', linewidth=0.8); ax3.axhline(0, color=\'black\', linewidth=0.8)\n    ax3.axvline(best_lag, color=\'tab:orange\', linestyle=\'--\')\n    ax3.annotate(f\'peak {best_lag:+d}d\', (best_lag, mean_curve.max()),\n                 textcoords=\'offset points\', xytext=(5, 5), color=\'tab:orange\')\n    ax3.set_xlabel(\'lag k (days): k > 0 means chatter TODAY vs price k days LATER\')\n    ax3.set_ylabel(\'correlation\'); ax3.set_title(\'lead/lag: peak right of 0 = chatter leads\')\n    ax3.legend(); ax3.grid(True, alpha=0.3)\n    fig.tight_layout(); plt.show()\n\n    print(f\'\\nlead/lag verdict: correlation peaks at {best_lag:+d} days \'\n          + (\'-> chatter LEADS price (tradeable window)\' if best_lag > 0 else\n         \'-> price moves first or same-day (chatter follows/chases)\'))\nelse:\n    print(\'no priced names with data in this window\')'

MD_14 = "## Does conviction LEAD price? (lead/lag scan)\n\nFor every plotted theme: correlate conviction_z today with the anchor ETF's\ndaily move k days away, k = -MAX_LAG..+MAX_LAG.\n\n- **Peak RIGHT of zero** → conviction today predicts price moves k days\n  later: conviction builds BEFORE the move (the tradeable case, and the\n  delay to use when acting on notebook 10's signals).\n- **Peak LEFT of zero** → price moves first, conviction follows: the crowd\n  is reacting, not anticipating (chasing).\n- Flat everywhere → conviction level carries no timing information for that\n  theme; changes (velocity) may still work where levels do not.\n\nThe second panel groups all theme-days into conviction quintiles and shows\nthe average forward move - the level-based version of the same question."

CODE_14 = "# ==== CONVICTION LEAD/LAG vs anchor ETF price ====\nMAX_LAG  = 20   # scan -MAX_LAG..+MAX_LAG days\nFWD_DAYS = 5    # forward-move horizon for the quintile panel\n\nconv_ll = pd.read_parquet(os.path.join(P, 'daily_theme_conviction.parquet'))\nconv_ll['date'] = pd.to_datetime(conv_ll['date']); conv_ll = clip_dates(conv_ll, 'date')\nprices_ll = load_prices()\npriced_ll = set(prices_ll['symbol'])\n\nlags = list(range(-MAX_LAG, MAX_LAG + 1))\ncurves, pairs = {}, []\nfor theme_name in themes:                      # the same themes plotted above\n    etf = THEME_ETFS.get(theme_name)\n    if etf not in priced_ll:\n        continue\n    one = prices_ll[prices_ll['symbol'] == etf].sort_values('date')\n    px = one.set_index('date')['px_last'].asfreq('D').ffill()\n    cz = (conv_ll[conv_ll['theme'] == theme_name].sort_values('date')\n          .set_index('date')['conviction_z'].asfreq('D').ffill())\n    dr = px.pct_change() * 100\n    curves[theme_name] = [cz.corr(dr.shift(-k)) for k in lags]\n    fwd = (px.shift(-FWD_DAYS) / px - 1) * 100\n    both = pd.DataFrame({'conv': cz, 'fwd_ret': fwd}).dropna()\n    both['theme'] = theme_name\n    pairs.append(both)\n\nif curves:\n    mean_curve = pd.DataFrame(curves, index=lags).mean(axis=1)\n    best_lag = int(mean_curve.idxmax()) if mean_curve.notna().any() else 0\n\n    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))\n    for nm, c in curves.items():\n        ax1.plot(lags, c, alpha=0.3, linewidth=1, label=nm)\n    ax1.plot(lags, mean_curve.values, linewidth=2.5, color='tab:blue', label='average')\n    ax1.axvline(0, color='black', linewidth=0.8); ax1.axhline(0, color='black', linewidth=0.8)\n    ax1.axvline(best_lag, color='tab:orange', linestyle='--')\n    ax1.annotate(f'peak {best_lag:+d}d', (best_lag, mean_curve.max()),\n                 textcoords='offset points', xytext=(5, 5), color='tab:orange')\n    ax1.set_xlabel('lag k (days): k > 0 = conviction TODAY vs ETF move k days LATER')\n    ax1.set_ylabel('correlation'); ax1.set_title('conviction lead/lag')\n    ax1.legend(fontsize=7); ax1.grid(True, alpha=0.3)\n\n    pairs = pd.concat(pairs)\n    bins = pd.qcut(pairs['conv'], 5, duplicates='drop')\n    summary = pairs.groupby(bins, observed=True)['fwd_ret'].agg(['mean', 'count'])\n    ax2.bar(range(len(summary)), summary['mean'],\n            color=['tab:red' if v < 0 else 'tab:green' for v in summary['mean']])\n    ax2.set_xticks(range(len(summary)))\n    ax2.set_xticklabels([f'{iv.left:.1f}' for iv in summary.index], rotation=45, fontsize=8)\n    ax2.axhline(0, color='black', linewidth=0.8)\n    ax2.set_xlabel('conviction_z quintile (lower edge)')\n    ax2.set_ylabel(f'avg next-{FWD_DAYS}d ETF move (%)')\n    ax2.set_title('forward move by conviction level'); ax2.grid(True, alpha=0.3)\n    fig.tight_layout(); plt.show()\n\n    print(f'lead/lag verdict: correlation peaks at {best_lag:+d} days '\n          + ('-> conviction LEADS the ETF (act with that delay)' if best_lag > 0 else\n         '-> the ETF moves first; conviction follows (chasing)'))\nelse:\n    print('no themes with priced anchors in this window')"

NA_V0 = '        m = (m / totals.replace(0, pd.NA)) * 100\n        m[totals < MIN_TOTAL] = pd.NA'

NA_V1 = "        # float('nan') keeps the series numeric (pd.NA would flip it to\n        # object dtype, which rolling/resample refuse to aggregate)\n        m = ((m / totals.replace(0, pd.NA)) * 100).astype('float64')\n        m[totals < MIN_TOTAL] = float('nan')"

NA_V2 = "        # keep everything float64: where() puts NaN where totals is zero,\n        # so no pd.NA ever enters the series (rolling/resample need floats)\n        totals = totals.astype('float64')\n        m = (m.astype('float64') / totals.where(totals > 0)) * 100\n        m[totals < MIN_TOTAL] = float('nan')"

# helper/patch_notebooks.py
# =========================
# One idempotent patcher for pending notebook updates. Safe to run any
# number of times - each patch applies once and is skipped afterwards.
#
#     python helper/patch_notebooks.py        (from the project root)
#
# Patches applied:
#   1. NA-dtype fix in the share-normalisation of notebooks 11/12/13
#      (pd.NA turned the series into object dtype, which rolling/resample
#      and astype all refuse - float64 + where() keeps it numeric).
#   2. "Chatter change -> price gradient + lead/lag" section appended to
#      notebooks 12 and 13, and "conviction lead/lag" appended to 14.
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def cell(kind, text):
    c = {"cell_type": kind, "metadata": {}, "source": text.splitlines(keepends=True)}
    if kind == "code":
        c["execution_count"] = None
        c["outputs"] = []
    return c


def load(nb):
    return json.load(open(f"notebooks/{nb}.ipynb", encoding="utf-8"))


def save(nb, d):
    with open(f"notebooks/{nb}.ipynb", "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
        f.write("\n")


# ---- patch 1: NA-dtype fix --------------------------------------------------
for nb in ["11_overlay_ticker_mentions", "12_overlay_ticker_first_derivative",
           "13_overlay_theme_first_derivative"]:
    d = load(nb)
    hits = 0
    for c in d["cells"]:
        if c["cell_type"] != "code":
            continue
        s = "".join(c["source"])
        for old in (NA_V1, NA_V0):
            if old in s:
                c["source"] = s.replace(old, NA_V2, 1).splitlines(keepends=True)
                hits += 1
                break
    if hits:
        save(nb, d)
        print(f"NA-dtype fix applied: {nb}")
    else:
        print(f"NA-dtype fix already in place: {nb}")

# ---- patch 2: lead/lag sections ---------------------------------------------
SECTIONS = {
    "12_overlay_ticker_first_derivative": (MD_1213, CODE_12, "LEAD/LAG (tickers)"),
    "13_overlay_theme_first_derivative": (MD_1213, CODE_13, "LEAD/LAG (themes)"),
    "14_overlay_theme_conviction": (MD_14, CODE_14, "CONVICTION LEAD/LAG"),
}
for nb, (md, code, marker) in SECTIONS.items():
    d = load(nb)
    # find an existing lead/lag CODE cell so a stale copy gets refreshed
    # in place rather than skipped (the code has been fixed over time)
    hits = [c for c in d["cells"]
            if c["cell_type"] == "code" and marker in "".join(c["source"])]
    if hits:
        if "".join(hits[0]["source"]) == code:
            print(f"lead/lag section already current: {nb}")
            continue
        hits[0]["source"] = code.splitlines(keepends=True)
        hits[0]["outputs"] = []
        save(nb, d)
        print(f"lead/lag section refreshed: {nb}")
        continue
    d["cells"].append(cell("markdown", md))
    d["cells"].append(cell("code", code))
    save(nb, d)
    print(f"lead/lag section added: {nb}")


# ---- patch 3: lag-aligned time-series cells ----------------------------------
MD_ALIGN = "## Lag-aligned view: shift the signal by its measured lead\n\nThe lead/lag scan above found each name's best lag k*. Here the signal is\nshifted FORWARD by k* days and drawn over price - if the lead is real, the\nshifted line's peaks should sit on top of the price moves they predicted.\nThe applied lag is printed on each chart title and marked with an arrow."
ALIGN = {"12_overlay_ticker_first_derivative": "# ==== LAG-ALIGNED TIME SERIES (tickers) ====\nSHOW_N = 3            # how many of the plotted names to draw\nif curves:\n    for name in names[:SHOW_N]:\n        if name not in curves:\n            continue\n        c = pd.Series(curves[name], index=lags)\n        k = int(c.idxmax()) if c.notna().any() else 0\n        m = (counts_ll[counts_ll['ticker'] == name].sort_values('date')\n             .set_index('date')['mention_count'].asfreq('D').fillna(0))\n        if NORMALISE:\n            totals = day_totals_ll.reindex(m.index).fillna(0).astype('float64')\n            m = (m.astype('float64') / totals.where(totals > 0)) * 100\n            m[totals < MIN_TOTAL] = float('nan')\n        px = price_series(prices_ll, name)\n        if px.empty:\n            continue\n        base = m.rolling(WINDOW, min_periods=1).mean() if NORMALISE else m.rolling(WINDOW).sum()\n        chg = base.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()\n        shifted = chg.shift(k)          # move the signal k days forward in time\n\n        fig, ax1 = plt.subplots(figsize=(13, 5))\n        ax1.axhline(0, color='black', linewidth=0.6)\n        ax1.plot(chg.index, chg.values, color='tab:green', linewidth=0.8,\n                 alpha=0.3, label='chatter change (original timing)')\n        ax1.plot(shifted.index, shifted.values, color='tab:green', linewidth=2,\n                 label=f'chatter change shifted {k:+d}d (measured lead)')\n        ax1.set_ylabel('chatter change', color='tab:green')\n        ax1.tick_params(axis='y', labelcolor='tab:green')\n        ax2 = ax1.twinx()\n        ax2.plot(px.index, px.values, color='tab:red', linewidth=1.6, label='price')\n        ax2.set_ylabel('price (USD)', color='tab:red'); ax2.tick_params(axis='y', labelcolor='tab:red')\n        if k != 0:\n            mid = shifted.dropna().index[len(shifted.dropna()) // 2]\n            ax1.annotate(f'lag applied: {k:+d} days', xy=(mid, ax1.get_ylim()[1] * 0.9),\n                         fontsize=10, color='tab:orange', fontweight='bold')\n        ax1.set_title(f'{name}: signal shifted by its lead ({k:+d}d, peak corr '\n                      f'{c.max():+.2f}) vs price - aligned peaks = real lead')\n        h1, l1 = ax1.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()\n        ax1.legend(h1 + h2, l1 + l2, loc='upper left', fontsize=8)\n        ax1.grid(True, alpha=0.3)\n        set_date_ticks(ax1, X_TICKS)\n        fig.tight_layout(); plt.show()",
         "13_overlay_theme_first_derivative": "# ==== LAG-ALIGNED TIME SERIES (themes) ====\nSHOW_N = 3            # how many of the plotted names to draw\nif curves:\n    for name in names[:SHOW_N]:\n        if name not in curves:\n            continue\n        c = pd.Series(curves[name], index=lags)\n        k = int(c.idxmax()) if c.notna().any() else 0\n        m = (counts_ll[counts_ll['theme'] == name].sort_values('date')\n             .set_index('date')['mention_count'].asfreq('D').fillna(0))\n        if NORMALISE:\n            totals = day_totals_ll.reindex(m.index).fillna(0).astype('float64')\n            m = (m.astype('float64') / totals.where(totals > 0)) * 100\n            m[totals < MIN_TOTAL] = float('nan')\n        px = price_series(prices_ll, THEME_ETFS.get(name, ''))\n        if px.empty:\n            continue\n        base = m.rolling(WINDOW, min_periods=1).mean() if NORMALISE else m.rolling(WINDOW).sum()\n        chg = base.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()\n        shifted = chg.shift(k)          # move the signal k days forward in time\n\n        fig, ax1 = plt.subplots(figsize=(13, 5))\n        ax1.axhline(0, color='black', linewidth=0.6)\n        ax1.plot(chg.index, chg.values, color='tab:green', linewidth=0.8,\n                 alpha=0.3, label='chatter change (original timing)')\n        ax1.plot(shifted.index, shifted.values, color='tab:green', linewidth=2,\n                 label=f'chatter change shifted {k:+d}d (measured lead)')\n        ax1.set_ylabel('chatter change', color='tab:green')\n        ax1.tick_params(axis='y', labelcolor='tab:green')\n        ax2 = ax1.twinx()\n        ax2.plot(px.index, px.values, color='tab:red', linewidth=1.6, label='price')\n        ax2.set_ylabel('price (USD)', color='tab:red'); ax2.tick_params(axis='y', labelcolor='tab:red')\n        if k != 0:\n            mid = shifted.dropna().index[len(shifted.dropna()) // 2]\n            ax1.annotate(f'lag applied: {k:+d} days', xy=(mid, ax1.get_ylim()[1] * 0.9),\n                         fontsize=10, color='tab:orange', fontweight='bold')\n        ax1.set_title(f'{name}: signal shifted by its lead ({k:+d}d, peak corr '\n                      f'{c.max():+.2f}) vs price - aligned peaks = real lead')\n        h1, l1 = ax1.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()\n        ax1.legend(h1 + h2, l1 + l2, loc='upper left', fontsize=8)\n        ax1.grid(True, alpha=0.3)\n        set_date_ticks(ax1, X_TICKS)\n        fig.tight_layout(); plt.show()",
         "14_overlay_theme_conviction": "# ==== LAG-ALIGNED TIME SERIES (conviction) ====\nSHOW_N = 3\nif curves:\n    for theme_name in list(curves)[:SHOW_N]:\n        c = pd.Series(curves[theme_name], index=lags)\n        k = int(c.idxmax()) if c.notna().any() else 0\n        etf = THEME_ETFS.get(theme_name)\n        one = prices_ll[prices_ll['symbol'] == etf].sort_values('date')\n        px = one.set_index('date')['px_last'].asfreq('D').ffill()\n        cz = (conv_ll[conv_ll['theme'] == theme_name].sort_values('date')\n              .set_index('date')['conviction_z'].asfreq('D').ffill())\n        shifted = cz.shift(k)\n\n        fig, ax1 = plt.subplots(figsize=(13, 5))\n        ax1.axhline(0, color='black', linewidth=0.6)\n        ax1.plot(cz.index, cz.values, color='tab:purple', linewidth=0.8,\n                 alpha=0.3, label='conviction_z (original timing)')\n        ax1.plot(shifted.index, shifted.values, color='tab:purple', linewidth=2,\n                 label=f'conviction_z shifted {k:+d}d (measured lead)')\n        ax1.set_ylabel('conviction z', color='tab:purple')\n        ax1.tick_params(axis='y', labelcolor='tab:purple')\n        ax2 = ax1.twinx()\n        ax2.plot(px.index, px.values, color='tab:red', linewidth=1.6, label=f'{etf} price')\n        ax2.set_ylabel('price (USD)', color='tab:red'); ax2.tick_params(axis='y', labelcolor='tab:red')\n        if k != 0:\n            mid = shifted.dropna().index[len(shifted.dropna()) // 2]\n            ax1.annotate(f'lag applied: {k:+d} days', xy=(mid, ax1.get_ylim()[1] * 0.9),\n                         fontsize=10, color='tab:orange', fontweight='bold')\n        ax1.set_title(f'{theme_name}: conviction shifted by its lead ({k:+d}d, '\n                      f'peak corr {c.max():+.2f}) vs {etf}')\n        h1, l1 = ax1.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()\n        ax1.legend(h1 + h2, l1 + l2, loc='upper left', fontsize=8)\n        ax1.grid(True, alpha=0.3)\n        set_date_ticks(ax1, X_TICKS)\n        fig.tight_layout(); plt.show()"}

for nb, code in ALIGN.items():
    d = load(nb)
    # skip if the shifted view OR its replacement (the spike-evidence view
    # from patch 5) is already there - otherwise every rerun of this script
    # would add one more copy that patch 5 then converts (duplicate cells)
    if any(("LAG-ALIGNED TIME SERIES" in "".join(c["source"])
            or "SPIKE EVIDENCE" in "".join(c["source"])) for c in d["cells"]):
        print(f"lag-aligned section already present: {nb}")
        continue
    d["cells"].append(cell("markdown", MD_ALIGN))
    d["cells"].append(cell("code", code))
    save(nb, d)
    print(f"lag-aligned section added: {nb}")

# ---- patch 4: notebook 10 thresholds become env-tunable (for the tuner) ------
NB10_PARAMS = [('K              = 2.0     # z threshold for the momentum triggers', "K              = float(os.environ.get('SIG_K', 2.0))     # z threshold for the momentum triggers (env-tunable)"), ('MIN_SCORE      = 4       # BUY needs at least this many of the 5 checks', "MIN_SCORE      = int(os.environ.get('SIG_MIN_SCORE', 4))       # BUY needs this many of the 5 checks (env-tunable)"), ('MIN_SCORE_SELL = 3       # bearish checks are harder to clear (bullish-bias floor)', "MIN_SCORE_SELL = int(os.environ.get('SIG_MIN_SCORE_SELL', 3))  # bearish floor (env-tunable)"), ('COOLDOWN_DAYS  = 14      # once a name signals BUY (or SELL), suppress the SAME', "COOLDOWN_DAYS  = int(os.environ.get('SIG_COOLDOWN', 14))       # suppress repeats of the SAME")]
d = load("10_trading_signals")
hits = 0
for c in d["cells"]:
    if c["cell_type"] != "code":
        continue
    s = "".join(c["source"])
    changed = False
    for old_p, new_p in NB10_PARAMS:
        if old_p in s:
            s = s.replace(old_p, new_p, 1)
            changed = True
            hits += 1
    if changed:
        c["source"] = s.splitlines(keepends=True)
if hits:
    save("10_trading_signals", d)
    print(f"notebook 10: {hits} thresholds now read SIG_* env overrides")
else:
    print("notebook 10: env-tunable thresholds already in place")


# ---- patch 5: spike-evidence charts REPLACE the lag-aligned (shifted) cells --
MD_SPIKE = '## Evidence view: chatter spikes marked on the price chart\n\nNo transformations - the price line is untouched. Every orange triangle is a\nday the chatter change spiked (top decile); the green shading covers the\nmeasured lead window after each spike. The headline number compares the\naverage price move in those windows against the baseline move over any\nwindow of the same length: **if the spikes systematically sit in front of\nabove-baseline moves, the signal leads price** - visible at a glance.'
SPIKE = {"12_overlay_ticker_first_derivative": "# ==== SPIKE EVIDENCE (tickers): chatter spikes marked on price ====\nSHOW_N  = 3       # how many of the plotted names to draw\nSPIKE_Q = 0.90    # a 'spike' = positive chatter change above this quantile\nMIN_GAP = 7       # days between counted spikes (first in a burst wins)\nif curves:\n    for name in names[:SHOW_N]:\n        if name not in curves:\n            continue\n        c = pd.Series(curves[name], index=lags)\n        k = max(int(c.idxmax()), 1) if c.notna().any() else 1            # measured lead, at least 1 day\n        m = (counts_ll[counts_ll['ticker'] == name].sort_values('date')\n             .set_index('date')['mention_count'].asfreq('D').fillna(0))\n        if NORMALISE:\n            totals = day_totals_ll.reindex(m.index).fillna(0).astype('float64')\n            m = (m.astype('float64') / totals.where(totals > 0)) * 100\n            m[totals < MIN_TOTAL] = float('nan')\n        px = price_series(prices_ll, name)\n        if px.empty:\n            continue\n        base_line = m.rolling(WINDOW, min_periods=1).mean() if NORMALISE else m.rolling(WINDOW).sum()\n        chg = base_line.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()\n\n        pos = chg[chg > 0].dropna()\n        if len(pos) < 10:\n            print(f'{name}: too few positive chatter days to mark spikes')\n            continue\n        thresh = pos.quantile(SPIKE_Q)\n        spikes = []\n        for d in chg[chg > thresh].dropna().index:\n            if not spikes or (d - spikes[-1]).days >= MIN_GAP:\n                spikes.append(d)\n        moves = []\n        for d in spikes:\n            p0 = px.asof(d); p1 = px.asof(d + pd.Timedelta(days=k))\n            if pd.notna(p0) and pd.notna(p1) and p0 != 0:\n                moves.append((p1 / p0 - 1) * 100)\n        baseline = ((px.shift(-k) / px - 1) * 100).mean()\n        if not moves:\n            print(f'{name}: spikes found but no forward prices to score')\n            continue\n        avg = sum(moves) / len(moves)\n        hit = 100 * sum(1 for x in moves if x > 0) / len(moves)\n\n        fig, ax = plt.subplots(figsize=(13, 5))\n        ax.plot(px.index, px.values, color='black', linewidth=1.4, label='price')\n        for i, d in enumerate(spikes):\n            ax.axvline(d, color='tab:orange', linestyle='--', linewidth=1, alpha=0.7)\n            ax.axvspan(d, d + pd.Timedelta(days=k), color='tab:green', alpha=0.10)\n            pa = px.asof(d)\n            if pd.notna(pa):\n                ax.scatter([d], [pa], marker='^', s=100, color='tab:orange',\n                           edgecolors='black', linewidths=0.8, zorder=5,\n                           label='chatter spike' if i == 0 else None)\n        ax.set_title(f'{name}: chatter spikes (^) precede price moves - measured lead {k}d\\n'\n                     f'{len(moves)} spikes | avg move in the next {k}d: {avg:+.1f}% '\n                     f'vs {baseline:+.1f}% baseline | positive {hit:.0f}% of the time')\n        ax.set_ylabel('price (USD)')\n        ax.legend(loc='upper left'); ax.grid(True, alpha=0.3)\n        set_date_ticks(ax, X_TICKS)\n        fig.tight_layout(); plt.show()",
         "13_overlay_theme_first_derivative": "# ==== SPIKE EVIDENCE (themes): chatter spikes marked on price ====\nSHOW_N  = 3       # how many of the plotted names to draw\nSPIKE_Q = 0.90    # a 'spike' = positive chatter change above this quantile\nMIN_GAP = 7       # days between counted spikes (first in a burst wins)\nif curves:\n    for name in names[:SHOW_N]:\n        if name not in curves:\n            continue\n        c = pd.Series(curves[name], index=lags)\n        k = max(int(c.idxmax()), 1) if c.notna().any() else 1            # measured lead, at least 1 day\n        m = (counts_ll[counts_ll['theme'] == name].sort_values('date')\n             .set_index('date')['mention_count'].asfreq('D').fillna(0))\n        if NORMALISE:\n            totals = day_totals_ll.reindex(m.index).fillna(0).astype('float64')\n            m = (m.astype('float64') / totals.where(totals > 0)) * 100\n            m[totals < MIN_TOTAL] = float('nan')\n        px = price_series(prices_ll, THEME_ETFS.get(name, ''))\n        if px.empty:\n            continue\n        base_line = m.rolling(WINDOW, min_periods=1).mean() if NORMALISE else m.rolling(WINDOW).sum()\n        chg = base_line.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()\n\n        pos = chg[chg > 0].dropna()\n        if len(pos) < 10:\n            print(f'{name}: too few positive chatter days to mark spikes')\n            continue\n        thresh = pos.quantile(SPIKE_Q)\n        spikes = []\n        for d in chg[chg > thresh].dropna().index:\n            if not spikes or (d - spikes[-1]).days >= MIN_GAP:\n                spikes.append(d)\n        moves = []\n        for d in spikes:\n            p0 = px.asof(d); p1 = px.asof(d + pd.Timedelta(days=k))\n            if pd.notna(p0) and pd.notna(p1) and p0 != 0:\n                moves.append((p1 / p0 - 1) * 100)\n        baseline = ((px.shift(-k) / px - 1) * 100).mean()\n        if not moves:\n            print(f'{name}: spikes found but no forward prices to score')\n            continue\n        avg = sum(moves) / len(moves)\n        hit = 100 * sum(1 for x in moves if x > 0) / len(moves)\n\n        fig, ax = plt.subplots(figsize=(13, 5))\n        ax.plot(px.index, px.values, color='black', linewidth=1.4, label='price')\n        for i, d in enumerate(spikes):\n            ax.axvline(d, color='tab:orange', linestyle='--', linewidth=1, alpha=0.7)\n            ax.axvspan(d, d + pd.Timedelta(days=k), color='tab:green', alpha=0.10)\n            pa = px.asof(d)\n            if pd.notna(pa):\n                ax.scatter([d], [pa], marker='^', s=100, color='tab:orange',\n                           edgecolors='black', linewidths=0.8, zorder=5,\n                           label='chatter spike' if i == 0 else None)\n        ax.set_title(f'{name}: chatter spikes (^) precede price moves - measured lead {k}d\\n'\n                     f'{len(moves)} spikes | avg move in the next {k}d: {avg:+.1f}% '\n                     f'vs {baseline:+.1f}% baseline | positive {hit:.0f}% of the time')\n        ax.set_ylabel('price (USD)')\n        ax.legend(loc='upper left'); ax.grid(True, alpha=0.3)\n        set_date_ticks(ax, X_TICKS)\n        fig.tight_layout(); plt.show()",
         "14_overlay_theme_conviction": "# ==== SPIKE EVIDENCE (conviction): crossings marked on ETF price ====\nSHOW_N   = 3\nCROSS_AT = 1.5    # a conviction event = conviction_z crossing above this\nMIN_GAP  = 10     # days between counted events\nif curves:\n    for theme_name in list(curves)[:SHOW_N]:\n        c = pd.Series(curves[theme_name], index=lags)\n        k = max(int(c.idxmax()), 1) if c.notna().any() else 1\n        etf = THEME_ETFS.get(theme_name)\n        one = prices_ll[prices_ll['symbol'] == etf].sort_values('date')\n        px = one.set_index('date')['px_last'].asfreq('D').ffill()\n        cz = (conv_ll[conv_ll['theme'] == theme_name].sort_values('date')\n              .set_index('date')['conviction_z'].asfreq('D').ffill())\n        crossings = cz[(cz > CROSS_AT) & (cz.shift(1) <= CROSS_AT)].index\n        events = []\n        for d in crossings:\n            if not events or (d - events[-1]).days >= MIN_GAP:\n                events.append(d)\n        moves = []\n        for d in events:\n            p0 = px.asof(d); p1 = px.asof(d + pd.Timedelta(days=k))\n            if pd.notna(p0) and pd.notna(p1) and p0 != 0:\n                moves.append((p1 / p0 - 1) * 100)\n        baseline = ((px.shift(-k) / px - 1) * 100).mean()\n        if not moves:\n            print(f'{theme_name}: no scoreable conviction crossings in this window')\n            continue\n        avg = sum(moves) / len(moves)\n        hit = 100 * sum(1 for x in moves if x > 0) / len(moves)\n\n        fig, ax = plt.subplots(figsize=(13, 5))\n        ax.plot(px.index, px.values, color='black', linewidth=1.4, label=f'{etf} price')\n        for i, d in enumerate(events):\n            ax.axvline(d, color='tab:purple', linestyle='--', linewidth=1, alpha=0.7)\n            ax.axvspan(d, d + pd.Timedelta(days=k), color='tab:green', alpha=0.10)\n            pa = px.asof(d)\n            if pd.notna(pa):\n                ax.scatter([d], [pa], marker='^', s=100, color='tab:purple',\n                           edgecolors='black', linewidths=0.8, zorder=5,\n                           label=f'conviction crosses +{CROSS_AT}' if i == 0 else None)\n        ax.set_title(f'{theme_name}: conviction events (^) precede {etf} moves - measured lead {k}d\\n'\n                     f'{len(moves)} events | avg move in the next {k}d: {avg:+.1f}% '\n                     f'vs {baseline:+.1f}% baseline | positive {hit:.0f}% of the time')\n        ax.set_ylabel('price (USD)')\n        ax.legend(loc='upper left'); ax.grid(True, alpha=0.3)\n        set_date_ticks(ax, X_TICKS)\n        fig.tight_layout(); plt.show()"}

for nb, code in SPIKE.items():
    d = load(nb)
    # pass 1: drop DUPLICATE spike/shifted cells (earlier script versions
    # could add one copy per run) - keep only the first of each kind
    kept, seen_code, seen_md = [], False, False
    for c in d["cells"]:
        src = "".join(c["source"])
        is_code = c["cell_type"] == "code" and ("LAG-ALIGNED TIME SERIES" in src
                                                or "SPIKE EVIDENCE" in src)
        is_md = c["cell_type"] == "markdown" and ("Lag-aligned view" in src
                                                  or "Evidence view" in src)
        if (is_code and seen_code) or (is_md and seen_md):
            continue                       # duplicate - drop it
        seen_code = seen_code or is_code
        seen_md = seen_md or is_md
        kept.append(c)
    deduped = len(kept) != len(d["cells"])
    d["cells"] = kept

    # pass 2: install/refresh the spike-evidence view in the surviving cell
    replaced = appended = False
    for c in d["cells"]:
        src = "".join(c["source"])
        if c["cell_type"] == "code" and ("LAG-ALIGNED TIME SERIES" in src
                                         or "SPIKE EVIDENCE" in src):
            if "DIRECTION FLIPS" in src or (
                    "SPIKE EVIDENCE" in src and src.strip() == code.strip()):
                replaced = "current"    # v2 flip cells (patch 6) supersede
                break
            c["source"] = code.splitlines(keepends=True)
            c["outputs"] = []
            c["execution_count"] = None
            replaced = True
        elif c["cell_type"] == "markdown" and ("Lag-aligned view" in src
                                               or "Evidence view" in src):
            c["source"] = MD_SPIKE.splitlines(keepends=True)
    if replaced == "current" and not deduped:
        print(f"spike-evidence already current: {nb}")
        continue
    if not replaced and replaced != "current":
        d["cells"].append(cell("markdown", MD_SPIKE))
        d["cells"].append(cell("code", code))
        appended = True
    save(nb, d)
    print(f"spike-evidence {'added' if appended else 'installed'}"
          + (" (duplicates removed)" if deduped else "") + f": {nb}")

# ---- patch 6: DIRECTION FLIPS replace the one-sided spike view ---------------
# The spike view only marked positive chatter bursts. This marks TURNING
# POINTS in both directions: the day the smoothed chatter change crosses
# from negative to positive (green up-triangle) and from positive to
# negative (red down-triangle), each with the average price move over the
# measured lead window - so the chart shows whether direction changes in
# attention anticipate direction changes in price.
MD_FLIP = """## Evidence view: chatter DIRECTION FLIPS marked on the price chart

No transformations - the price line is untouched. A green up-triangle marks
the day the smoothed chatter change TURNS POSITIVE (crowd re-engaging), a
red down-triangle the day it TURNS NEGATIVE (crowd losing interest). The
shading covers the measured lead window after each flip; the title compares
the average price move after each direction against the baseline drift.
**If green flips sit in front of rallies and red flips in front of fades,
attention direction leads price direction** - visible at a glance."""

FLIP = {}
FLIP["12_overlay_ticker_first_derivative"] = """# ==== SPIKE EVIDENCE v2 - DIRECTION FLIPS (tickers): turning points on price ====
SHOW_N   = 3      # how many of the plotted names to draw
MIN_GAP  = 7      # days between counted flips (first in a burst wins)
EPS_STD  = 0.25   # noise floor: a flip only counts once |change| clears this
                  # many std devs - micro-wiggles around zero are ignored
if curves:
    for name in names[:SHOW_N]:
        if name not in curves:
            continue
        c = pd.Series(curves[name], index=lags)
        k = max(int(c.idxmax()), 1) if c.notna().any() else 1
        m = (counts_ll[counts_ll['ticker'] == name].sort_values('date')
             .set_index('date')['mention_count'].asfreq('D').fillna(0))
        if NORMALISE:
            totals = day_totals_ll.reindex(m.index).fillna(0).astype('float64')
            m = (m.astype('float64') / totals.where(totals > 0)) * 100
            m[totals < MIN_TOTAL] = float('nan')
        px = price_series(prices_ll, name)
        if px.empty:
            continue
        base_line = m.rolling(WINDOW, min_periods=1).mean() if NORMALISE else m.rolling(WINDOW).sum()
        chg = base_line.diff().rolling(DERIV_SMOOTH, min_periods=1).mean()
        eps = float(chg.std() * EPS_STD) if chg.notna().any() else 0.0

        # state machine: +1 once change clears +eps, -1 once it clears -eps.
        # A flip = entering the opposite state (hysteresis kills noise).
        up, down, state = [], [], 0
        for d, v in chg.dropna().items():
            if state <= 0 and v > eps:
                if not up or (d - up[-1]).days >= MIN_GAP:
                    up.append(d)
                state = 1
            elif state >= 0 and v < -eps:
                if not down or (d - down[-1]).days >= MIN_GAP:
                    down.append(d)
                state = -1

        def fwd_moves(events):
            out = []
            for d in events:
                p0 = px.asof(d); p1 = px.asof(d + pd.Timedelta(days=k))
                if pd.notna(p0) and pd.notna(p1) and p0 != 0:
                    out.append((p1 / p0 - 1) * 100)
            return out
        up_mv, dn_mv = fwd_moves(up), fwd_moves(down)
        baseline = ((px.shift(-k) / px - 1) * 100).mean()
        if not up_mv and not dn_mv:
            print(f'{name}: no scoreable direction flips in this window')
            continue

        fig, ax = plt.subplots(figsize=(13, 5))
        ax.plot(px.index, px.values, color='black', linewidth=1.4, label='price')
        for i, d in enumerate(up):
            ax.axvspan(d, d + pd.Timedelta(days=k), color='tab:green', alpha=0.08)
            pa = px.asof(d)
            if pd.notna(pa):
                ax.scatter([d], [pa], marker='^', s=110, color='tab:green',
                           edgecolors='black', linewidths=0.8, zorder=5,
                           label='chatter change turns POSITIVE' if i == 0 else None)
        for i, d in enumerate(down):
            ax.axvspan(d, d + pd.Timedelta(days=k), color='tab:red', alpha=0.08)
            pa = px.asof(d)
            if pd.notna(pa):
                ax.scatter([d], [pa], marker='v', s=110, color='tab:red',
                           edgecolors='black', linewidths=0.8, zorder=5,
                           label='chatter change turns NEGATIVE' if i == 0 else None)
        up_s = (f'{len(up_mv)} pos flips avg {sum(up_mv)/len(up_mv):+.1f}%'
                if up_mv else 'no pos flips')
        dn_s = (f'{len(dn_mv)} neg flips avg {sum(dn_mv)/len(dn_mv):+.1f}%'
                if dn_mv else 'no neg flips')
        ax.set_title(f'{name}: chatter direction flips vs price - moves over the next {k}d\\n'
                     f'{up_s} | {dn_s} | baseline {baseline:+.1f}%')
        ax.set_ylabel('price (USD)')
        ax.legend(loc='upper left'); ax.grid(True, alpha=0.3)
        set_date_ticks(ax, X_TICKS)
        fig.tight_layout(); plt.show()"""

FLIP["13_overlay_theme_first_derivative"] = (
    FLIP["12_overlay_ticker_first_derivative"]
    .replace("(tickers): turning points", "(themes): turning points")
    .replace("counts_ll['ticker'] == name", "counts_ll['theme'] == name")
    .replace("px = price_series(prices_ll, name)",
             "px = price_series(prices_ll, THEME_ETFS.get(name, ''))"))

FLIP["14_overlay_theme_conviction"] = """# ==== SPIKE EVIDENCE v2 - DIRECTION FLIPS (conviction): crossings on ETF price ====
SHOW_N   = 3
CROSS_AT = 1.5    # an event = conviction_z crossing +CROSS_AT (bullish) or
MIN_GAP  = 10     # -CROSS_AT (bearish); days between counted events
if curves:
    for theme_name in list(curves)[:SHOW_N]:
        c = pd.Series(curves[theme_name], index=lags)
        k = max(int(c.idxmax()), 1) if c.notna().any() else 1
        etf = THEME_ETFS.get(theme_name)
        one = prices_ll[prices_ll['symbol'] == etf].sort_values('date')
        px = one.set_index('date')['px_last'].asfreq('D').ffill()
        cz = (conv_ll[conv_ll['theme'] == theme_name].sort_values('date')
              .set_index('date')['conviction_z'].asfreq('D').ffill())

        def spaced(idx, gap):
            out = []
            for d in idx:
                if not out or (d - out[-1]).days >= gap:
                    out.append(d)
            return out
        up = spaced(cz[(cz > CROSS_AT) & (cz.shift(1) <= CROSS_AT)].index, MIN_GAP)
        down = spaced(cz[(cz < -CROSS_AT) & (cz.shift(1) >= -CROSS_AT)].index, MIN_GAP)

        def fwd_moves(events):
            out = []
            for d in events:
                p0 = px.asof(d); p1 = px.asof(d + pd.Timedelta(days=k))
                if pd.notna(p0) and pd.notna(p1) and p0 != 0:
                    out.append((p1 / p0 - 1) * 100)
            return out
        up_mv, dn_mv = fwd_moves(up), fwd_moves(down)
        baseline = ((px.shift(-k) / px - 1) * 100).mean()
        if not up_mv and not dn_mv:
            print(f'{theme_name}: no scoreable conviction crossings in this window')
            continue

        fig, ax = plt.subplots(figsize=(13, 5))
        ax.plot(px.index, px.values, color='black', linewidth=1.4, label=f'{etf} price')
        for i, d in enumerate(up):
            ax.axvspan(d, d + pd.Timedelta(days=k), color='tab:green', alpha=0.08)
            pa = px.asof(d)
            if pd.notna(pa):
                ax.scatter([d], [pa], marker='^', s=110, color='tab:green',
                           edgecolors='black', linewidths=0.8, zorder=5,
                           label=f'conviction crosses +{CROSS_AT}' if i == 0 else None)
        for i, d in enumerate(down):
            ax.axvspan(d, d + pd.Timedelta(days=k), color='tab:red', alpha=0.08)
            pa = px.asof(d)
            if pd.notna(pa):
                ax.scatter([d], [pa], marker='v', s=110, color='tab:red',
                           edgecolors='black', linewidths=0.8, zorder=5,
                           label=f'conviction crosses -{CROSS_AT}' if i == 0 else None)
        up_s = (f'{len(up_mv)} bullish avg {sum(up_mv)/len(up_mv):+.1f}%'
                if up_mv else 'no bullish crossings')
        dn_s = (f'{len(dn_mv)} bearish avg {sum(dn_mv)/len(dn_mv):+.1f}%'
                if dn_mv else 'no bearish crossings')
        ax.set_title(f'{theme_name}: conviction crossings vs {etf} - moves over the next {k}d\\n'
                     f'{up_s} | {dn_s} | baseline {baseline:+.1f}%')
        ax.set_ylabel('price (USD)')
        ax.legend(loc='upper left'); ax.grid(True, alpha=0.3)
        set_date_ticks(ax, X_TICKS)
        fig.tight_layout(); plt.show()"""

for nb, code in FLIP.items():
    d = load(nb)
    replaced = appended = False
    for c in d["cells"]:
        src = "".join(c["source"])
        if c["cell_type"] == "code" and ("SPIKE EVIDENCE" in src
                                         or "LAG-ALIGNED TIME SERIES" in src):
            if "DIRECTION FLIPS" in src and src.strip() == code.strip():
                replaced = "current"
                break
            c["source"] = code.splitlines(keepends=True)
            c["outputs"] = []
            c["execution_count"] = None
            replaced = True
        elif c["cell_type"] == "markdown" and ("Evidence view" in src
                                               or "Lag-aligned view" in src):
            c["source"] = MD_FLIP.splitlines(keepends=True)
    if replaced == "current":
        print(f"direction-flip evidence already current: {nb}")
        continue
    if not replaced:
        d["cells"].append(cell("markdown", MD_FLIP))
        d["cells"].append(cell("code", code))
        appended = True
    save(nb, d)
    print(f"direction-flip evidence {'added' if appended else 'installed'}: {nb}")

# ---- patch 7: report-card headline horizon 10d -> 20d -------------------------
# The horizon analysis showed the signal's edge peaks around 3-4 weeks, so
# the headline hit rate / avg return in the 15/16 report cards now uses the
# 20-day forward return. The 5/10/20d bar chart keeps all three horizons.
H20 = [("ret_10d", "ret_20d"),
       ("10d hit rate", "20d hit rate"),
       ("avg 10d return", "avg 20d return"),
       ("10d forward return", "20d forward return"),
       ("(10d horizon)", "(20d horizon)"),
       ("10-day outcome", "20-day outcome")]
for nb in ["15_overlay_trading_signals", "16_overlay_theme_trading_signals"]:
    d = load(nb)
    hits = 0
    for c in d["cells"]:
        if c["cell_type"] != "code":
            continue
        s = "".join(c["source"])
        if "ret_10d" not in s and "10d hit rate" not in s:
            continue
        for old, new in H20:
            if old in s:
                s = s.replace(old, new)
                hits += 1
        c["source"] = s.splitlines(keepends=True)
        c["outputs"] = []
        c["execution_count"] = None
    if hits:
        save(nb, d)
        print(f"report-card horizon 10d -> 20d ({hits} spots): {nb}")
    else:
        print(f"report-card horizon already 20d: {nb}")

# ---- patch 8: signal triangles must sit ON the plotted price line -------------
# Two causes of floating triangles: (a) the marker height came from the
# DAILY price series while the line can be weekly-resampled (px_line), and
# (b) a signal newer than the last price pull was asof'd to the last known
# price - a triangle hovering past the end of the line. Fix: take the height
# from the plotted line itself, and skip (with a note) signals outside the
# price data span.
TRI_OLD = "        price_at = px_daily.asof(mid)"
TRI_NEW = """        if px_line.empty or not (px_line.index.min() <= mid <= px_line.index.max()):
            print(f'  note: signal {mid.date()} is outside the price data span - '
                  'rerun pull_bloomberg_prices.py to see it on the chart')
            continue
        price_at = px_line.asof(mid)"""
for nb in ["15_overlay_trading_signals", "16_overlay_theme_trading_signals"]:
    d = load(nb)
    hits = 0
    for c in d["cells"]:
        if c["cell_type"] != "code":
            continue
        s = "".join(c["source"])
        if TRI_OLD in s:
            c["source"] = s.replace(TRI_OLD, TRI_NEW).splitlines(keepends=True)
            c["outputs"] = []
            c["execution_count"] = None
            hits += 1
    if hits:
        save(nb, d)
        print(f"triangle alignment fixed: {nb}")
    else:
        print(f"triangle alignment already fixed: {nb}")


# ---- patch 9: notebook 16 - the SIGNAL COMPONENTS panel ------------------------
# Below the signal charts: for each plotted theme, price + signal markers on
# top, and underneath the exact ingredients notebook 10 used to decide -
# attention z, conviction z (trailing 84d baselines, live-parity) and the
# 5-day sentiment change - so every triangle can be traced to what fired it.
MD_COMP = """## Signal components: WHAT fired each BUY/SELL

For each theme above: price with the signal triangles (top) and the exact
ingredients notebook 10 weighed (bottom) - attention z (crowd size),
conviction z (crowd size x bullish lean), both against TRAILING 84-day
baselines exactly as the signal generator computes them, plus the 5-day
sentiment change on the right axis. Dashed lines mark +/-K, the trigger
threshold. Vertical bands mark the signal dates - trace any triangle down
to see which ingredient(s) crossed."""

CODE_COMP = """# ==== SIGNAL COMPONENTS over time (notebook 10's exact ingredients) ====
import numpy as np
from src.themes import build_ticker_to_themes

ROLL_C, BASE_C, MIND_C = 7, 84, 28          # same as notebook 10
K_GUIDE = float(os.environ.get('SIG_K', 2.0))

cnt_c = pd.read_parquet(os.path.join(P, 'daily_ticker_counts.parquet'))
cnt_c['date'] = pd.to_datetime(cnt_c['date']); cnt_c = clip_dates(cnt_c, 'date')
lookup_c = build_ticker_to_themes()
cnt_c['themes'] = cnt_c['ticker'].map(lambda t: lookup_c.get(t, []))
td_c = (cnt_c.explode('themes').dropna(subset=['themes'])
        .groupby(['date', 'themes'])['mention_count'].sum().reset_index())
days_c = pd.date_range(cnt_c['date'].min(), cnt_c['date'].max(), freq='D')
wide_c = (td_c.pivot_table(index='date', columns='themes', values='mention_count')
          .reindex(days_c).fillna(0))

def trailing_z_c(frame):
    r = frame.rolling(ROLL_C, min_periods=1).sum()
    mu = r.rolling(BASE_C, min_periods=MIND_C).mean()
    sd = r.rolling(BASE_C, min_periods=MIND_C).std().replace(0, np.nan)
    return (r - mu) / sd

att_c = trailing_z_c(wide_c)

ts_c = pd.read_parquet(os.path.join(P, 'daily_theme_sentiment.parquet'))
ts_c['date'] = pd.to_datetime(ts_c['date']); ts_c = clip_dates(ts_c, 'date')
wn_c = ts_c.pivot_table(index='date', columns='theme', values='n_posts').reindex(days_c).fillna(0)
wb_c = ts_c.pivot_table(index='date', columns='theme', values='net_bullish').reindex(days_c)
pressure_c = (wn_c * wb_c).fillna(0)
conv_c = trailing_z_c(pressure_c)
share_c = (pressure_c.rolling(ROLL_C, min_periods=1).sum()
           / wn_c.rolling(ROLL_C, min_periods=1).sum().replace(0, np.nan))
sent_c = share_c.diff(5)

for theme_name in themes_ranked:
    if theme_name not in conv_c.columns:
        print(f'{theme_name}: no sentiment series in this window - skipped')
        continue
    s = sig_all[sig_all['theme'] == theme_name]
    symbol = s['symbol'].iloc[0]
    px = price_series(prices, symbol)
    if px.empty:
        continue
    fig, (axp, axz) = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                                   gridspec_kw={'height_ratios': [2, 1.6]})
    axp.plot(px.index, px.values, color='black', linewidth=1.3, label=f'{symbol} price')
    for _, row in s.iterrows():
        d0 = row['action_date']
        if not (px.index.min() <= d0 <= px.index.max()):
            continue
        color = 'tab:green' if row['action'] == 'BUY' else 'tab:red'
        mk = '^' if row['action'] == 'BUY' else 'v'
        axp.scatter([d0], [px.asof(d0)], marker=mk, s=110, color=color,
                    edgecolors='black', linewidths=0.8, zorder=5)
        axz.axvline(d0, color=color, alpha=0.25, linewidth=1.2)
    axp.set_ylabel('price (USD)')
    axp.set_title(f'{theme_name}: price + signals (top) and the ingredients '
                  'that fired them (bottom)')
    axp.grid(True, alpha=0.3); axp.legend(loc='upper left', fontsize=8)

    if theme_name in att_c.columns:
        axz.plot(att_c.index, att_c[theme_name], color='tab:orange',
                 linewidth=1.2, label='attention z')
    axz.plot(conv_c.index, conv_c[theme_name], color='tab:purple',
             linewidth=1.2, label='conviction z')
    axz2 = axz.twinx()
    axz2.plot(sent_c.index, sent_c[theme_name], color='tab:blue',
              linewidth=1.0, alpha=0.6, label='sentiment 5d change')
    axz2.set_ylabel('sent 5d chg', color='tab:blue')
    axz2.tick_params(axis='y', labelcolor='tab:blue')
    axz.axhline(K_GUIDE, color='green', linestyle='--', linewidth=0.9)
    axz.axhline(-K_GUIDE, color='red', linestyle='--', linewidth=0.9)
    axz.axhline(0, color='black', linewidth=0.6)
    axz.set_ylabel('z-score (trailing)')
    h1, l1 = axz.get_legend_handles_labels(); h2, l2 = axz2.get_legend_handles_labels()
    axz.legend(h1 + h2, l1 + l2, loc='upper left', fontsize=8)
    axz.grid(True, alpha=0.3)
    set_date_ticks(axz, X_TICKS)
    fig.tight_layout(); plt.show()"""

# REVERSED (user request): the components panel is REMOVED if present.
d = load("16_overlay_theme_trading_signals")
before_n = len(d["cells"])
d["cells"] = [c for c in d["cells"]
              if "SIGNAL COMPONENTS" not in "".join(c["source"])
              and "Signal components" not in "".join(c["source"])]
if len(d["cells"]) != before_n:
    save("16_overlay_theme_trading_signals", d)
    print(f"signal-components panel removed ({before_n - len(d['cells'])} cells): "
          "16_overlay_theme_trading_signals")
else:
    print("signal-components panel not present (nothing to remove)")

# ---- patch 10: DELIBERATE signals - fewer, higher-confidence defaults ---------
# K 2.0 -> 2.5 (only the top ~1% most abnormal days trigger, not ~2%),
# SELL floor 3 -> 4 of 5 checks (sells were the weak side - they now need
# the same weight of evidence as buys), cooldown 14 -> 21 days (one episode,
# one trade). Still env-tunable, so the tuner can revisit these later.
STRICT = [("os.environ.get('SIG_K', 2.0)", "os.environ.get('SIG_K', 2.5)"),
          ("os.environ.get('SIG_MIN_SCORE_SELL', 3)",
           "os.environ.get('SIG_MIN_SCORE_SELL', 4)"),
          ("os.environ.get('SIG_COOLDOWN', 14)",
           "os.environ.get('SIG_COOLDOWN', 21)")]
d = load("10_trading_signals")
hits = 0
for c in d["cells"]:
    if c["cell_type"] != "code":
        continue
    s = "".join(c["source"])
    changed = False
    for old_v, new_v in STRICT:
        if old_v in s:
            s = s.replace(old_v, new_v, 1)
            changed = True
            hits += 1
    if changed:
        c["source"] = s.splitlines(keepends=True)
        c["outputs"] = []
        c["execution_count"] = None
if hits:
    save("10_trading_signals", d)
    print(f"deliberate-signal defaults set ({hits} thresholds): 10_trading_signals")
else:
    print("deliberate-signal defaults already in place: 10_trading_signals")

# ---- patch 11: notebook 16 - OPPORTUNITY GAP (strong but untradeable) ---------
# Emerging/auto themes and unanchored hand themes are deliberately kept OUT
# of the tradeable book. This plot is the bridge: it ranks recent conviction
# for EVERY tracked theme, colours tradeable vs tracked-only differently,
# and lists the tracked-only themes scoring above the tradeable median -
# the shortlist for 'should I get an instrument approved for this?'.
MD_GAP = """## Opportunity gap: strong themes you CANNOT trade yet

Grey bars = themes with an approved instrument (the tradeable book).
Orange bars = tracked-only themes: no approved instrument (crypto,
cannabis, small caps) and auto-promoted emerging themes. An orange bar to
the RIGHT of the dashed line (the tradeable median) is retail conviction
the desk currently has no way to express - the shortlist for requesting a
new instrument on the approved list."""

CODE_GAP = """# ==== OPPORTUNITY GAP: conviction of tradeable vs tracked-only themes ====
GAP_DAYS = 30     # 'recent' = average conviction over the last N days

conv_g = pd.read_parquet(os.path.join(P, 'daily_theme_conviction.parquet'))
conv_g['date'] = pd.to_datetime(conv_g['date'])
conv_g = clip_dates(conv_g, 'date')
if len(conv_g):
    recent_g = conv_g[conv_g['date'] >= conv_g['date'].max()
                      - pd.Timedelta(days=GAP_DAYS)]
    score_g = recent_g.groupby('theme')['conviction_z'].mean().sort_values()

    from src.themes import THEME_ETFS as _ANCHORS
    tradeable_g = score_g.index.isin(_ANCHORS)
    med_g = score_g[tradeable_g].median() if tradeable_g.any() else 0.0

    fig, ax = plt.subplots(figsize=(11, max(5, 0.28 * len(score_g))))
    ax.barh(range(len(score_g)), score_g.values,
            color=['tab:gray' if t else 'tab:orange' for t in tradeable_g])
    ax.set_yticks(range(len(score_g)))
    ax.set_yticklabels(score_g.index, fontsize=8)
    ax.axvline(med_g, color='black', linestyle='--', linewidth=1,
               label=f'tradeable median ({med_g:+.2f})')
    ax.axvline(0, color='black', linewidth=0.6)
    ax.set_xlabel(f'avg conviction z, last {GAP_DAYS} days')
    ax.set_title('grey = tradeable book | orange = tracked only (no instrument)')
    ax.legend(); ax.grid(True, alpha=0.3, axis='x')
    fig.tight_layout(); plt.show()

    gap_list = score_g[(~tradeable_g) & (score_g > med_g)]
    if len(gap_list):
        print('tracked-only themes ABOVE the tradeable median - consider '
              'requesting an instrument:')
        for t, v in gap_list.sort_values(ascending=False).items():
            print(f'  {t:<28} avg conviction z {v:+.2f}')
    else:
        print('no tracked-only theme currently out-scores the tradeable book.')
else:
    print('no conviction data in this window')"""

d = load("16_overlay_theme_trading_signals")
if any("OPPORTUNITY GAP" in "".join(c["source"]) for c in d["cells"]):
    print("opportunity-gap plot already present: 16_overlay_theme_trading_signals")
else:
    d["cells"].append(cell("markdown", MD_GAP))
    d["cells"].append(cell("code", CODE_GAP))
    save("16_overlay_theme_trading_signals", d)
    print("opportunity-gap plot added: 16_overlay_theme_trading_signals")

# ---- patch 12: notebook 16 ranks themes by TRADE CERTAINTY, not volume --------
# 'Top' used to mean most-signalled. Now it means the best trade to make:
#   certainty = score (breadth: how many of the 5 checks agreed)
#             + |conviction z| capped at 3 (strength: how abnormal the crowd)
#             + recency bonus fading over 90 days (a live edge beats an old one)
# Each theme is ranked by its BEST signal, and the ranking is printed with
# the reasons so the order is never a mystery.
RANK_OLD = """themes_ranked = sig_all['theme'].value_counts().head(HOW_MANY).index.tolist()
print('auto themes (most signalled):', themes_ranked)"""
RANK_NEW = """# rank themes by CERTAINTY - the best trade first, not the loudest theme
cert = sig_all.copy()
cert['strength'] = cert['conv_z'].abs().clip(upper=3)
_age = (cert['action_date'].max() - cert['action_date']).dt.days
cert['recency'] = (1 - _age / 90).clip(lower=0)
cert['certainty'] = cert['score'] + cert['strength'] + cert['recency']
theme_cert = cert.groupby('theme')['certainty'].max().sort_values(ascending=False)
themes_ranked = theme_cert.head(HOW_MANY).index.tolist()
print('themes ranked by trade CERTAINTY (score + |conv z| + recency):')
for t in themes_ranked:
    b = cert[cert['theme'] == t].sort_values('certainty').iloc[-1]
    print(f"  {t:<26} certainty {b['certainty']:.2f} | best: {b['action']} "
          f"score {b['score']}/5, conv z {b['conv_z']:+.2f}, {b['action_date'].date()}")"""
d = load("16_overlay_theme_trading_signals")
hits = 0
for c in d["cells"]:
    if c["cell_type"] != "code":
        continue
    s = "".join(c["source"])
    if RANK_OLD in s:
        c["source"] = s.replace(RANK_OLD, RANK_NEW).splitlines(keepends=True)
        c["outputs"] = []
        c["execution_count"] = None
        hits += 1
if hits:
    save("16_overlay_theme_trading_signals", d)
    print("certainty ranking installed: 16_overlay_theme_trading_signals")
else:
    print("certainty ranking already installed (or pattern moved): "
          "16_overlay_theme_trading_signals")

# ---- patch 13: triangles SNAP to a vertex of the drawn line -------------------
# asof() returns the last value AT OR BEFORE the marker date, but when the
# line is resampled (FREQ='W') matplotlib interpolates BETWEEN weekly points,
# so an asof height can sit off the visible line. Snapping the marker to the
# NEAREST plotted point makes 'off the line' geometrically impossible (the
# marker x moves by at most half a plot period).
SNAP_OLD = "        price_at = px_line.asof(mid)"
SNAP_NEW = """        _i = px_line.index.get_indexer([mid], method='nearest')[0]
        mid = px_line.index[_i]           # snap to a vertex of the drawn line
        price_at = px_line.iloc[_i]"""
for nb in ["15_overlay_trading_signals", "16_overlay_theme_trading_signals"]:
    d = load(nb)
    hits = 0
    for c in d["cells"]:
        if c["cell_type"] != "code":
            continue
        s = "".join(c["source"])
        if SNAP_OLD in s:
            c["source"] = s.replace(SNAP_OLD, SNAP_NEW).splitlines(keepends=True)
            c["outputs"] = []
            c["execution_count"] = None
            hits += 1
    if hits:
        save(nb, d)
        print(f"triangle snap-to-line installed: {nb}")
    else:
        print(f"triangle snap-to-line already installed: {nb}")

# ---- patch 14: DISPLAY conviction becomes TRAILING (live-parity) --------------
# Notebooks 08/09 z-scored bull pressure against the WHOLE window's mean -
# the 2021 volume mania dragged that mean so high that every normal-volume
# day since plots as negative ('quieter than the window average' masquerading
# as 'bearish'). The signal engine (notebook 10) already uses TRAILING
# baselines; this makes the display charts use the same rule, so charts and
# signals finally agree: each day is scored ONLY against its preceding 84
# days, no future information, no mania distortion.
CONV_OLD = "conviction_z = (roll_bp - roll_bp.mean()) / roll_bp.std().replace(0, np.nan)"
CONV_NEW = """# TRAILING z (live-parity, same rule as notebook 10): each day scored
# against the PRECEDING 84 days only - no future info, and quiet periods
# are no longer dragged negative by the 2021 volume mania
_mu = roll_bp.rolling(84, min_periods=28).mean()
_sd = roll_bp.rolling(84, min_periods=28).std().replace(0, np.nan)
conviction_z = (roll_bp - _mu) / _sd"""
for nb in ["08_ticker_conviction", "09_theme_conviction"]:
    d = load(nb)
    hits = 0
    for c in d["cells"]:
        if c["cell_type"] != "code":
            continue
        s = "".join(c["source"])
        if CONV_OLD in s:
            c["source"] = s.replace(CONV_OLD, CONV_NEW).splitlines(keepends=True)
            c["outputs"] = []
            c["execution_count"] = None
            hits += 1
    if hits:
        save(nb, d)
        print(f"trailing conviction installed: {nb}")
    else:
        print(f"trailing conviction already installed: {nb}")

# ---- patch 15: TRADE LEDGER after the report card in 15/16 --------------------
# One row per trade with SIGNED P&L at every horizon (a SELL profits when
# the price falls, so positive always reads 'money made'), newest first,
# plus closed-trade totals. This is the 'show me every trade' table.
MD_LEDGER = """## Trade ledger: every trade and its P&L

One row per signal, newest first. P&L is SIGNED trade profit - a SELL
profits when the price falls - so a positive number always means money
made, whichever side the trade was. Trades younger than the horizon have
no P&L yet (still open)."""

CODE_LEDGER = """# ==== TRADE LEDGER: every trade, signed P&L per horizon ====
if len(perf):
    ledger = perf.copy()
    if 'theme' not in ledger.columns and 'theme' in sig_all.columns:
        ledger = ledger.merge(
            sig_all[['symbol', 'action_date', 'action', 'theme']]
            .drop_duplicates(),
            on=['symbol', 'action_date', 'action'], how='left')
    sign = ledger['action'].map({'BUY': 1, 'SELL': -1})
    for h in HORIZONS:
        ledger[f'pnl_{h}d_%'] = (ledger[f'ret_{h}d'] * sign).round(2)
    ledger = ledger.sort_values('action_date', ascending=False)
    show_cols = [c for c in ['action_date', 'action', 'theme', 'symbol',
                             'score'] if c in ledger.columns]
    show_cols += [f'pnl_{h}d_%' for h in HORIZONS]
    closed = ledger['pnl_20d_%'].dropna()
    print(f'TRADE LEDGER - {len(ledger)} trades, newest first '
          '(signed P&L: positive = money made)')
    if len(closed):
        print(f'closed 20d trades: {len(closed)} | total P&L '
              f'{closed.sum():+.1f}% | avg {closed.mean():+.2f}% | '
              f'winners {(closed > 0).mean() * 100:.0f}%')
    else:
        print('no trade old enough to close a 20d hold yet')
    # ---- per-side breakdown: the BUY book and the SELL book separately ----
    for side in ('BUY', 'SELL'):
        part = ledger[ledger['action'] == side]
        if not len(part):
            print(f'\\n--- {side}: no trades in this window ---')
            continue
        pc = part['pnl_20d_%'].dropna()
        print(f'\\n--- {side} trades: {len(part)} ---')
        if len(pc):
            print(f'closed: {len(pc)} | total {pc.sum():+.1f}% | '
                  f'avg {pc.mean():+.2f}% | winners {(pc > 0).mean() * 100:.0f}% | '
                  f'best {pc.max():+.2f}% | worst {pc.min():+.2f}%')
        else:
            print('(all still open)')
        print(part[show_cols].to_string(index=False))
else:
    print('no scored trades in this window')"""

for nb in ["15_overlay_trading_signals", "16_overlay_theme_trading_signals"]:
    d = load(nb)
    # refresh an existing ledger cell in place if its code has moved on
    hits = [c for c in d["cells"]
            if c["cell_type"] == "code" and "TRADE LEDGER" in "".join(c["source"])]
    if hits:
        if "".join(hits[0]["source"]).strip() == CODE_LEDGER.strip():
            print(f"trade ledger already current: {nb}")
            continue
        hits[0]["source"] = CODE_LEDGER.splitlines(keepends=True)
        hits[0]["outputs"] = []
        hits[0]["execution_count"] = None
        save(nb, d)
        print(f"trade ledger refreshed (per-side breakdown): {nb}")
        continue
    # first install: insert right AFTER the report-card cell so the ledger
    # sits with the 20d numbers it itemises
    idx = None
    for i, c in enumerate(d["cells"]):
        if c["cell_type"] == "code" and "perf = pd.DataFrame(rows)" in "".join(c["source"]):
            idx = i
            break
    if idx is None:
        print(f"report-card cell not found - ledger skipped: {nb}")
        continue
    d["cells"][idx + 1:idx + 1] = [cell("markdown", MD_LEDGER),
                                   cell("code", CODE_LEDGER)]
    save(nb, d)
    print(f"trade ledger added: {nb}")

print("all patches processed (v12).")

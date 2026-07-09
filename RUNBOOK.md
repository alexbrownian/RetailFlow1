# RUNBOOK — what to run, when

Every command runs from the project root (`RetailFlow1/`) in PowerShell.
Most days you only need **one** command: `python update_data.py`.

## Quick reference

| I want to… | Run |
|---|---|
| Refresh everything (LIVE, fast) | `python update_data.py` — pulls a WEEK of top posts, splices the aggregate tail, reruns 08/09/10 + overlays. Minutes. |
| Backtest a fixed window | set `END_DATE` in `update_data.py`, then `python update_data.py` — auto-runs the FULL chain (01-10), no fetching |
| Full rebuild while live | `python update_data.py --full` — use after changing START_DATE or to rebuild sentiment from scratch |
| Recompute without any API call | `python update_data.py --skip-fetch` |
| Check my FetchLayer key is set | `python api_calls/fetch_all.py --check` |
| Prove the key works (1 credit) | `python api_calls/fetch_all.py --test` |
| See what live data landed | `python check_live_ingestion.py` |
| Pull Bloomberg prices | `python pull_bloomberg_prices.py` |
| Preview the Bloomberg pull only | `python pull_bloomberg_prices.py --dry-run` |
| Compare data vs price | open notebooks **11–16**, Run All (auto-picks tickers) |
| Open the dashboard | `python -m streamlit run dashboard/app.py` |

## The one switch: the window

At the top of `update_data.py`:

```python
START_DATE = "2021-01-01"   # inclusive
END_DATE   = ""             # "" = LIVE (to today);  "2021-11-01" = backtest window
PRICE_TOP_N = 150           # how many top-mentioned tickers Bloomberg pulls
```

`END_DATE = ""` → **live fast path**: fetch a week of the most popular posts
(Reddit top-of-week + new; X Top + Latest), splice the last ~45 days of the
aggregates from `posts.parquet`, rerun the signal notebooks (08/09/10), pull
prices, render overlays 11-14. Minutes, and the 7-day lookback the trading
signals run on is always fully covered.

A date → **backtest**: the FULL chain (01-10) rebuilds every aggregate for
exactly that window, then the overlays draw against prices for the same
window. To check the model in a past regime: set the dates, run the one
command, open 11-14 and read the plots. This one place drives the pipeline,
the Bloomberg pull, and the overlay charts (`--start/--end` override it for
one run, and the override reaches the notebooks and the price pull too).

**Broken-notebook safety (new):** every run first validates all notebooks
and auto-restores truncated ones from git; executed notebooks are written to
a temp file and swapped in atomically - an interrupted run can never
truncate a notebook again.

---

## Initial setup (do this once per machine)

**Both machines:**

```powershell
pip install -r requirements.txt --user
```

Then create a `.env` file in the project root with your key
(`FETCHLAYER_KEY=...`). StockTwits needs no key; `.env` is never committed.

**Work laptop — hydrate the committed data into place (first time only):**

```powershell
git pull
python -c "from src import gic_data; gic_data.hydrate()"
```

`hydrate()` copies the five committed aggregates from `GIC_RAW_DATA/` into
`data/processed/`, which is where the notebooks and dashboard look. Without
this first hydrate a fresh clone has an empty `data/processed/` and the
consumer notebooks find nothing. After this, `update_data.py` keeps the two in
sync automatically on every run — you only hydrate by hand this one time.

(For the Bloomberg overlays, also install `blpapi` once — see the Bloomberg
scenario below.)

## Scenario: everyday live refresh

Same command on **both** machines — it auto-detects which one it's on:

```powershell
python update_data.py
```

- **Personal laptop** (has `posts.parquet`): appends live posts into it,
  splices the aggregate tail (fast - no 01-07 rerun), reruns 08/09/10 +
  overlays, publishes `GIC_RAW_DATA`, safety-checks it. Pass `--full` when
  you want the whole chain rebuilt.
- **Work laptop** (no `posts.parquet`): folds live posts into `GIC_RAW_DATA`,
  hydrates, rebuilds signals, safety-checks it.

Then commit the updated abstracted data:

```powershell
git add GIC_RAW_DATA
git commit -m "live update"
git push
```

## Scenario: test the FetchLayer key (no waste)

```powershell
python api_calls/fetch_all.py --check     # which sources will be called? (no API call)
python api_calls/fetch_all.py --test      # ONE real call, prints posts, writes nothing
```

Expect `--check` to show: CALL StockTwits, CALL Reddit, SKIP X. If Reddit says
SKIP, your `.env` here is missing `FETCHLAYER_KEY`.

## Scenario: backtest / look at a past regime

1. Edit the window in `update_data.py`, e.g. `START_DATE="2021-01-01"`,
   `END_DATE="2021-11-01"`.
2. Recompute for that window (backtest mode skips fetching automatically
   and runs the full 01-10 rebuild):
   ```powershell
   python update_data.py
   ```
3. Match the prices, then look:
   ```powershell
   python pull_bloomberg_prices.py
   ```
   open notebooks 11–14 → they clip themselves to the same window.

(On the work laptop you can skip step 2 — the committed aggregates already
span history; just set the dates, pull prices, and open the notebooks.)

## Scenario: set up + pull Bloomberg prices (work laptop only)

One-time setup (Terminal must be installed and logged in):

```powershell
python -m pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi --user
python -c "import blpapi; print('blpapi', blpapi.__version__)"
```

Then, with the Terminal running:

```powershell
python pull_bloomberg_prices.py
```

Writes `data/prices/prices.parquet` (`date, symbol, px_last`). This file stays
local — it is gitignored (Bloomberg redistribution terms).

## Scenario: compare my data against price (overlays)

Just open and Run All — no typing, they auto-pick the names from the data.
Ordered mentions → velocity → conviction → signals, tickers before themes:

- **11** most-mentioned tickers: mentions (share of chatter) vs price
- **12** most-mentioned tickers: attention first-derivative vs price
- **13** most-mentioned themes: attention first-derivative vs anchor ETF
- **14** highest-sentiment themes: conviction vs the theme's ETF price
- **15** most-signalled symbols: BUY/SELL conviction clusters + report card + high-conviction backtest (PnL/Sharpe)
- **16** same as 15, themes only

`HOW_MANY` at the top of each just controls how many charts to draw.

## Scenario: transfer producer → work laptop

**Producer** (after `update_data.py`): `git add GIC_RAW_DATA && git commit && git push`.

**Work laptop**:
```powershell
git pull
python update_data.py          # hydrates + rebuilds; or --skip-fetch to just view
```

---

## Which notebooks run where

- **Work laptop (no raw text):** 08, 09, 10 (signals/conviction) and 11–14
  (overlays). **Not** 01–07 — they need `posts.parquet`.
- **Personal laptop:** all of them.

## If something looks off

- `python check_live_ingestion.py` — shows what raw/derived/committed data is
  fresh vs stale.
- The safety line at the end of `update_data.py` must say **PASS** before you
  commit `GIC_RAW_DATA` — it confirms no raw text leaked into the aggregates.
- Close Jupyter/Excel/the dashboard before a run (Windows locks the parquet
  files); if a write fails, the script prints the manual `ren` fix.

# RUNBOOK — what to run, when

Every command runs from the project root (`RetailFlow1/`).
Most days only one command is needed: `python update_data.py`.

## Quick reference

| Task | Command |
|---|---|
| Refresh everything (LIVE, fast) | `python update_data.py` — pulls the lookback window of top posts, splices the aggregate tail, reruns 08/09/10 + overlays 11–16 |
| Backtest / view a past window | set `END_DATE` in `update_data.py`, then `python update_data.py` — instant: the aggregates are window-independent, only the overlays re-render |
| Build aggregates over ALL history | `python update_data.py --full` — run ONCE (and after changing BUILD_START_DATE or the theme definitions); afterwards every window has data |
| Recompute without API calls | `python update_data.py --skip-fetch` |
| Check the FetchLayer key | `python api_calls/fetch_all.py --check` (no calls) / `--test` (1 credit) |
| See what live data landed | `python check_live_ingestion.py` |
| Pull Bloomberg prices | `python pull_bloomberg_prices.py` (`--dry-run` to preview) |
| Refresh only the overlays | `python run_overlays.py` |
| Run the tests | `python -m pytest tests/ -v` |

## The one switch: the window

```python
START_DATE = "2021-01-01"   # inclusive
END_DATE   = ""             # "" = LIVE (to today);  "2021-11-01" = backtest window
PRICE_TOP_N = 150           # how many top-mentioned tickers the price pull covers
```

`END_DATE = ""` → live fast path (minutes). A date → a frozen backtest VIEW —
instant, because the aggregates are built once over `BUILD_START_DATE` →
today (`--full`) and every window is just a lens over them. The window drives
the Bloomberg pull and the overlay charts; `--start/--end` override it for a
single run. A **WINDOW CHECK** in every run's output flags, per source,
whether the chosen window actually has data — an empty chart is never a
mystery.

Every run prints a **data coverage table** (posts per month, per source) so
gaps are visible immediately, validates and auto-repairs the notebooks, and
ends with the text-free **safety check** on ABSTRACTED_DATA.

## Which notebooks run where

| Notebooks | External machine (raw store) | Internal machine (Bloomberg) |
|---|---|---|
| 01–07 (raw text: slice, mentions, sentiment) | full chain / `--full` | never — they need `posts.parquet`, which must not exist there |
| 08–10 (conviction + signals, text-free) | every run | every run |
| 11–16 (price overlays) | every run (needs prices) | every run — the Terminal lives here |

## Initial setup (once per machine)

```powershell
pip install -r requirements.txt --user
```

Create `.env` in the project root with the FetchLayer key
(`FETCHLAYER_KEY=...`). `.env` is never committed. StockTwits needs no key.

**Internal machine, first time only:**

```powershell
git pull
python -c "from src import abstracted_data; abstracted_data.hydrate()"
```

`hydrate()` copies the five committed aggregates from `ABSTRACTED_DATA/`
into `data/processed/`, where the notebooks look. After this one step,
`update_data.py` keeps the two in sync automatically.

For the Bloomberg overlays, install blpapi once (Terminal running):

```powershell
python -m pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi --user
python -c "import blpapi; print('blpapi', blpapi.__version__)"
```

## Everyday live refresh

The same command on both machines — it auto-detects which one it is on:

```powershell
python update_data.py
```

- **External machine**: merges live posts into the raw store, splices the
  aggregate tail (fast — no 01–07 rerun; pass `--full` for a rebuild),
  reruns 08/09/10 + overlays, publishes ABSTRACTED_DATA.
- **Internal machine**: folds live posts into ABSTRACTED_DATA, hydrates,
  rebuilds the signals, pulls prices, renders the overlays.

Then commit the updated aggregates:

```powershell
git add ABSTRACTED_DATA
git commit -m "live update"
git push
```

## Backtest / study a past regime

1. Set the window in `update_data.py`, e.g. `START_DATE="2021-01-01"`,
   `END_DATE="2021-11-01"`.
2. `python update_data.py` — backtest mode skips fetching automatically and
   runs the full 01–10 rebuild.
3. `python pull_bloomberg_prices.py`, then open notebooks 11–16 — they clip
   themselves to the same window.

(On the internal machine step 2's rebuild is unnecessary — the committed
aggregates already span history; set the dates, pull prices, open the
notebooks.)

## The overlays (open and Run All — they auto-pick names from the data)

- **11** most-mentioned tickers: mention share vs price
- **12** most-mentioned tickers: attention first derivative vs price
- **13** most-mentioned themes: attention first derivative vs anchor ETF
- **14** highest-sentiment themes: conviction vs the theme's ETF price
- **15** most-signalled symbols: BUY/SELL conviction clusters + report card
- **16** the same, themes only

`HOW_MANY` at the top of each controls how many charts to draw;
`PLOT_LAST_DAYS` zooms to the recent stretch; `X_TICKS` sets label density.

## Transfer: external → internal

External machine (after `update_data.py`): `git add ABSTRACTED_DATA && git commit && git push`.

Internal machine: `git pull`, then `python update_data.py`.

## If something looks off

- `python check_live_ingestion.py` — freshness of every layer, in flow order.
- The **DATA COVERAGE** table in the run output shows exactly which months
  have data, per source ('.' = a real gap).
- The safety line at the end of every run must say **PASS** before
  committing ABSTRACTED_DATA.
- Close Jupyter/Excel before a run (Windows locks the parquet files); if a
  write fails, the script prints the manual rename fix.

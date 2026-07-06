# Week 2 — from mention signals to the Stage-1 verdict

Week 1 built the machine: cleaned dataset (Reddit + X), word-ticker
screening, chained notebooks, take-off detection. Week 2 answers the ONLY
question that matters for Stage 1: **do mention spikes LEAD price moves?**
Work through the steps in order — each produces something the next needs.

---

## Step 1 — Get the X data in (~30-60 min, mostly download time)

```bash
pip install huggingface_hub fsspec zstandard
python data_ingestion/scripts/fetch_x_data.py     # 3 datasets -> data/raw/X Data/
python data_ingestion/scripts/add_x_data.py       # rebuilds posts.parquet with source column
```

- The mjw dataset is millions of rows — if RAM is tight, fetch one at a
  time: `python data_ingestion/scripts/fetch_x_data.py --only stock_market_tweets`
- `add_x_data.py` prints the final tweet count → paste it into
  `EXPECTED_X_ROWS` in `tests/test_pipeline.py`.
- Run `pytest tests/ -v` — everything should be green before you continue.

**Know your X coverage map:** 2015 → mid-2020, then Nov 2023 →. There is a
**gap covering 2021-2023**, so the GME squeeze window is Reddit-only.

## Step 2 — Choose the analysis windows (5 min of thought, in notebook 01)

Set the TIME WINDOW in notebook 01 (nowhere else — the chain inherits it).
Recommended: run the whole of Week 2 **twice**, once per window:

| window | why |
|---|---|
| `2020-03-01` → `2020-08-01` | COVID crash + recovery; both sources active; SPX-heavy X data |
| `2023-10-01` → `2025-01-01` | modern era; both sources active; overlaps MAGS's life |

(The classic `2021` window still works for Reddit-only take-off analysis.)

## Step 3 — Regenerate the signals (a few minutes each)

Run in order: **01** (slice + screening) → **02** (ticker counts + the
Reddit-vs-X lead/lag section) → **03** (take-offs) → **04** (theme counts) →
**05** (theme take-offs). The theme scanner was rewritten (tokenise once +
hash lookups instead of 20 regex passes per post): measured ~12k posts/sec,
so a full 2.8M-post window is ~4 minutes, not hours.

Sanity checks before moving on: notebook 02's top-20 table should be real
tickers (no LOAN/EDGE); the "who moves first" table should print verdicts;
notebook 04 should save `daily_theme_counts.parquet` for the four themes
that map to the ETFs (`gold_metals`, `ai_megacap`, `crypto`, `semiconductors`).

## Step 4 — Get the price data (the missing input)

Create `data/prices/` with one CSV per ETF, **exactly two columns**:

```
date,close
2020-03-02,148.62
2020-03-03,151.03
```

| file | instrument | note |
|---|---|---|
| `GLD.csv`  | GLD US Equity | gold |
| `MAGS.csv` | MAGS US Equity | **exists only since Apr 2023** — use for the 2023+ window only |
| `BTC.csv`  | BTC-USD | trades 7 days/week (see Step 5 alignment note) |
| `SMH.csv`  | SMH US Equity | semiconductors |

From **Bloomberg** (either works):
- Excel add-in: `=BDH("GLD US Equity","PX_LAST","2020-01-01","2025-12-31")`,
  then save the two columns as CSV.
- Terminal: load the ticker, `GP` chart → Export → Excel, keep date + last
  price, rename columns to `date,close`.

Use **unadjusted last price is fine** for this analysis (we only use daily
% changes; splits would matter, ETFs rarely split — spot-check the series
for any absurd one-day jump).

## Step 5 — THE BACKTEST: do mentions lead returns?

This is Stage 1 step 4, the core deliverable. The method, and *why* each
piece is there:

### 5a. Turn both series into stationary signals

- **Prices → returns:** `ret[t] = close[t] / close[t-1] - 1`.
  WHY: price *levels* trend, and any two trending series correlate
  (spuriously). Returns wiggle around zero, so a correlation with them
  actually means something.
- **Mentions → changes:** use the day-over-day change of `log(1+count)`
  (≈ daily % growth of attention), or the rolling z-score from notebook 02.
  Same reason — raw counts trend with subreddit growth.

### 5b. Align the calendars

Mentions exist 7 days/week; GLD/MAGS/SMH trade 5. Roll weekend mentions
forward into Monday (that models "weekend chatter hits the Monday open"):

```python
mentions_td = mentions.resample('D').sum()
mentions_td.index = mentions_td.index.map(next_trading_day)   # Sat/Sun -> Mon
mentions_td = mentions_td.groupby(level=0).sum()
```

For **BTC** skip this — it trades every day; align on calendar days.

### 5c. Cross-correlation at lags (the same maths as the Reddit-vs-X cell)

For each theme/ETF pair, compute `corr(mention_change[t], ret[t+k])` for
`k = -10 .. +10`:

- peak at **k > 0** → today's mentions correlate with returns k days
  **later** → **mentions LEAD price** (this is what you're hoping for)
- peak at **k < 0** → price moved first, Reddit/X is reacting (still
  interesting — it kills the signal for trading)
- peak at **k = 0** → same-day: can't tell who caused whom.

### 5d. Event study on the take-off days (more intuitive than correlation)

Take notebook 03/05's flagged rise days. For each, record the forward
return of the matching ETF over the next 1, 3, 5, 10 trading days. Compare
against the baseline (average forward returns over ALL days in the window):

```
                +1d     +3d     +5d     +10d
take-off days   +0.4%   +1.1%   +1.3%   +0.9%
all days        +0.1%   +0.2%   +0.4%   +0.8%   <- baseline
```

If the take-off rows consistently beat the baseline, the inflection signal
has predictive lift. If not, mentions may still *coincide* with moves
without leading them.

### 5e. Sanity-check against luck (permutation test)

Daily correlations are small numbers, and with 21 lags × 4 themes ×
2 windows you WILL find something by chance. The junior-friendly guard:
**circularly shift** the mention series by a random offset (e.g. 50-200
days), recompute the best-lag correlation, repeat ~200 times. If your real
correlation isn't clearly bigger than ~95% of the shuffled ones, treat it
as noise. Also: a real signal should show the **same sign and similar lag
in both windows** — consistency beats magnitude.

What counts as promising at daily frequency: |corr| of 0.05-0.15 with the
right sign, stable across windows, plus visible event-study lift. Don't
expect 0.5 — nobody gets that.

## Step 6 — Calibrate K (the take-off threshold)

Grid-search `K = 1.5, 2.0, 2.5, 3.0, 3.5` in notebook 03: for each K,
re-run the event study (5d) and record the lift and the number of flagged
days. Pick K on ONE window, then confirm it still works on the other —
picking and confirming on the same data is how you fool yourself. Write
down the chosen K and why (README already reserves Stage 1 step 5 for this).

## Step 7 — Write it up, decide Stage 2

For each of the four themes answer: does the mention signal lead, lag, or
coincide? at what lag? does the take-off event study show lift? was it
consistent across windows? Then the go/no-go: if raw mentions show nothing
anywhere, that is the trigger for **Stage 2 (sentiment)** — direction-aware
scoring might rescue what volume alone can't see.

---

## Pitfalls checklist (tape this to the monitor)

- [ ] **Correlate returns, never price levels** (spurious trend correlation).
- [ ] **No look-ahead:** a mention on day t may only predict returns from
      t+1 onwards; same-day correlation is not a trading signal.
- [ ] **Weekends:** roll weekend mentions into Monday (except BTC).
- [ ] **MAGS starts Apr 2023** — exclude it from the 2020 window.
- [ ] **Score-maturity bias:** use RAW mention counts for the backtest
      (weighted counts mix Reddit upvotes with mjw Twitter likes).
- [ ] **Multiple testing:** permutation test + two-window consistency.
- [ ] **X coverage gap 2021-2023:** cross-source claims only where both exist.

## Want it built for you?

The backtest deserves proper code: `src/price_lag.py` (returns, calendar
alignment, cross-correlation, event study, permutation test — all unit
tested) plus `notebooks/06_mention_price_lag.ipynb` reading
`data/prices/*.csv` and the theme counts. Ask Claude to build it in the
next session — this file is the spec.

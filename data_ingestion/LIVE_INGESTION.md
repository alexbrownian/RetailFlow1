# Live data ingestion — the playbook

_What we learned from the fintwit-bot project (StephanAkkerman — same
author as our X datasets), what we adopted, what we kept our own, and
exactly how to wire live sources when the time comes._

---

## What we adopted from fintwit-bot vs what we kept (and why)

| Element | Their approach | Our verdict |
|---|---|---|
| **Sentiment engine** | FinTwitBERT-sentiment (a BERT fine-tuned on finance tweets, 3 labels bullish/neutral/bearish) | **KEPT VADER+WSB for the historical backfill, ADOPT FinTwitBERT for live** (see below — this is a volume argument, not a quality argument) |
| **StockTwits** | Polls public JSON endpoints, no key needed | **ADOPTED** — `api_calls/fetch_stocktwits.py` + `src/stocktwits_data.py`. Killer feature: users label their OWN posts Bullish/Bearish → free ground truth to calibrate our lexicon against |
| **Discord** | Full bot account posting embeds to channels (their whole UI) | **REJECTED (2026-07-06)** — our output is the Streamlit DASHBOARD only, which now includes a Signals panel showing notebook 10's BUY/SELL decisions with reasons. (Also: fintwit-bot does NOT ingest FROM Discord, and neither do we — reading other people's servers has consent/ToS problems) |
| **Reddit ingestion** | asyncpraw, `@loop(hours=12)`, keeps seen-ids for 72h then forgets | **ADOPT the PRAW pattern, KEEP our dedup**: their 72h id memory can re-ingest an old post that resurfaces; our "first seen wins" against the full parquet id set is strictly safer |
| **X / Twitter** | No official API — a logged-in session's cURL (`curl.txt`) replays the HomeLatestTimeline request | **DOCUMENTED, NOT ADOPTED (yet)** — it works but is fragile (breaks when X changes internals) and violates X ToS; weigh that before using. Our frozen HF dumps stay the historical X source |
| **Market analytics** | 5-min "overview" loop: top mentioned tickers with per-ticker bullish/neutral/bearish counts and deltas | **Already have the equivalents** (notebook 06 monitor, dashboard); ADOPTED their bull/bear COUNT breakdown idea → calibration output below |
| **Chart-recognizer** (image model: is this post a chart?) | Classifies tweet images | **NOT adopted** — our sources are text-first; revisit if image-heavy sources join |
| **Config toggles** | `config.yaml` turns every feature on/off | **Not needed** — our notebooks/params play that role |

### The sentiment verdict, in full

FinTwitBERT is genuinely better at classifying a single finance tweet
than VADER (it was trained for exactly that). We still keep VADER for the
**10.8M-post historical backfill** because BERT on CPU ≈ 50–200 posts/sec
→ days of compute, vs minutes with the parallel VADER path — and our
signals aggregate hundreds of posts, where lexicon noise largely averages
out. **Live is a different regime**: a day's new posts are thousands, not
millions, so FinTwitBERT becomes affordable (~1 min/day on CPU). The
upgrade path is already built: swap the engine inside
`src/sentiment.py::score_text()` and everything downstream (06/07/08/09)
is untouched. Before switching, run the **StockTwits calibration**: score
a month of StockTwits messages with BOTH engines and compare each against
the authors' own Bullish/Bearish labels — adopt FinTwitBERT live only if
it beats VADER on that ground truth. (`author_label()` in
`src/stocktwits_data.py` extracts the labels from the raw files.)

---

## Where API keys live

**All credentials go in `.env` at the project root.** `.env` and
`curl.txt` are git-ignored — keys never appear in code, notebooks, or
commits. Scripts parse `.env` directly (plain Python, no `python-dotenv`
needed), with `os.environ` as a fallback.

**All live fetchers live in `api_calls/` — run ONE file:**
`python api_calls/fetch_all.py`. It checks `.env` first and calls only
the sources whose keys are filled (empty key = source ignored, no request
sent). `--check` prints the key check without calling anything.

| Credential | Used by | How to get it |
|---|---|---|
| `FETCHLAYER_KEY` | `api_calls/fetch_reddit_live.py` (preferred Reddit backend) | fetchlayer.dev dashboard → copy key into `.env`. 1 credit per request; test with `python api_calls/test_fetchlayer.py` (1 credit) |
| `REDDIT_APP_NAME`, `REDDIT_PERSONAL_USE`, `REDDIT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD` | `api_calls/fetch_reddit_live.py` (official fallback backend) | https://old.reddit.com/prefs/apps/ → create app, type **script** → the id under the name = PERSONAL_USE, the secret = SECRET. Consider a dedicated account |
| StockTwits | `api_calls/fetch_stocktwits.py` | none needed (public read-only streams) |
| `FETCHLAYER_KEY` (again) | `api_calls/fetch_x_live.py` (**preferred X backend**) | the SAME FetchLayer key also drives X via `POST /api/twitter/search` (product=Latest) — **no paid X account needed**. This is the path that works today |
| `X_BEARER_TOKEN` | `api_calls/fetch_x_live.py` (official v2 API fallback) | developer.x.com → sign up for a paid tier → Projects & Apps → your app → Bearer Token. Paste into `.env`; used only if no FetchLayer key is present |
| X (unofficial) | not built | the curl.txt session-replay trick — fragile + ToS risk, see above |

### X official API — what paying gets you, and the quota maths

`fetch_x_live.py` queries the **v2 recent-search** endpoint (last 7 days)
with cashtag queries built from your THEME_ETFS anchors + retail
favourites, chunked ~12 cashtags per query to fit the query-length limit,
`lang:en -is:retweet`. Tweets append to `data/raw/X Data/x_api_live.csv.zst`,
which is a REGISTERED dataset (`x_api_live` in `src/x_data.py`) — so
`add_x_data.py` merges it like any other X source, and real tweet ids
share the `x_` prefix with the historical dumps (overlaps dedupe
automatically, first seen wins).

**Check current tier pricing/caps at developer.x.com before scheduling** —
they change. The structural facts: reads are capped per MONTH and
per-15-minutes, so the budget seat belt is `--max-tweets` per run × runs
per day. Rough guide: a Basic-style tier (~10-15k reads/month) supports
about **2-3 runs/day with `--max-tweets 150`**; hourly runs need a
Pro-style tier. The fetcher stops instantly on HTTP 429 and the next run
catches up. Remember the README checklist: live X levels will NOT match
the frozen-dump levels — trailing z-scores only, warm-up applies.

## Rate limits & polling cadences

| Source | Hard limits | Our cadence | Notes |
|---|---|---|---|
| **Reddit (PRAW, OAuth)** | 100 requests/min; each listing call returns ≤100 items, `/new` pagination caps ~1000 posts/listing | poll `/new` per subreddit every **15 min** (15 subs ≈ 60 calls/hour — far under limit) | On a viral day a busy sub can exceed 1000 posts between long gaps → poll often, and accept that live coverage < archive coverage (backtest handicap rule) |
| **StockTwits (unauth)** | ~200 requests/hour per IP; 429 = stop | **hourly**, ~40 symbols ≈ 40 calls (fintwit-bot polls 6-hourly; hourly is still 5× under the cap) | the fetcher stops early on 429 and the next run catches up |
| **X via curl.txt** | undocumented; session-based | fintwit-bot: continuous timeline loop | fragile + ToS risk — decide deliberately |

## The live pipeline, end to end

```
                       (every 15 min)                (hourly)
  Reddit /new ──► normalise() [clean_data.py] ──┐    StockTwits streams ──► raw .jsonl.zst
                                                ▼                              │
                              skip ids already in posts.parquet                │
                              ("first seen wins" — the dedup contract)         │
                                                ▼                              ▼
                              append to posts.parquet (or partitioned     normalise_stocktwits()
                              posts/YYYY-MM.parquet — see README)          source='stocktwits'
                                                └───────────┬──────────────────┘
                                                            ▼  (daily, after midnight UTC)
                                    notebook 01 (extend window!) → 02 → 06 → 07 → 09
                                                            ▼
                                     trade_signals.parquet SNAPSHOT (dated, never revised)
                                                            ▼
                              THE DASHBOARD (streamlit run dashboard/app.py) - the single
                              output surface: signals panel, mentions, sentiment, momentum
```

Daily-run rules (all cross-referenced in the README live-data checklist):
1. **Extend notebook 01's END_DATE** (or set None) — otherwise new data is
   silently excluded.
2. **Delete/invalidate the sentiment cache** after each ingestion
   (`posts_slice_sentiment.parquet` — row-count change does it
   automatically when the slice grows).
3. **Snapshot the signals**: copy each day's `trade_signals.parquet` to a
   dated file. Never recompute history — the snapshot IS the record the
   backtest-to-live comparison uses.
4. **Warm-up**: the first ~28 live days have no trailing z — expected.
5. Watch the pinned tests: totals move on every ingestion (update
   deliberately), and the date assertion needs bumping past 2026.

## Automation: update_data.py + Task Scheduler + the dashboard button

`update_data.py` (project root) is the one-command orchestrator:
fetch enabled sources → merge if new X raw exists → execute the notebook
chain **in place** (01→02→06→07→09, so the notebooks always hold fresh
outputs) → snapshot `trade_signals*.parquet` to
`data/processed/signal_snapshots/<date>_...` (never revised). Logs land in
`logs/run_<date>.log`. `--dry-run` prints the plan; `--skip-fetch`
recomputes without API calls.

**One-time prerequisite:** notebook 01's `END_DATE = None`, so the window
extends automatically as data arrives (START_DATE fixed at your chosen
history depth, e.g. '2023-10-01').

**Schedule it daily** (Windows): Task Scheduler → Create Basic Task →
Daily 06:30 → Start a Program →
`Program: python`, `Arguments: update_data.py`,
`Start in: C:\Users\alexd\Desktop\GIC\RetailFlow1`. Or from PowerShell:

```powershell
schtasks /Create /SC DAILY /ST 06:30 /TN "RetailFlow daily" ^
  /TR "python C:\Users\alexd\Desktop\GIC\RetailFlow1\update_data.py"
```

**Or push the button:** the dashboard side
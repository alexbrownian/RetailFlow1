#!/usr/bin/env python
"""
update_data.py - the single pipeline entry point. Set the window, run it.

    python update_data.py

TWO MODES, ONE KNOB (the window at the top of this file):

  LIVE MODE      END_DATE = ""   (the day-to-day default)
      Fast path. Fetch the lookback window of the most popular posts from
      every live source (FetchLayer Reddit + X, StockTwits), splice them
      into the aggregates incrementally, recompute the signal notebooks
      (08/09/10), pull prices and render the overlay notebooks (11-16).

  BACKTEST MODE  END_DATE = "YYYY-MM-DD"
      View a past regime - instant. The aggregates are WINDOW-INDEPENDENT
      (built once over BUILD_START_DATE -> today via --full), so a backtest
      only re-renders the overlays for the chosen view window. Nothing is
      rebuilt and nothing is fetched (pass --fetch to force).

    Examples:
        START_DATE="2021-01-01"; END_DATE="2021-11-01"   -> view Jan-Oct 2021
        START_DATE="2021-01-01"; END_DATE=""             -> LIVE, 2021 -> today

RUNS ON BOTH MACHINES (auto-detected)
    * EXTERNAL machine (data/processed/posts.parquet present)
        Holds the raw post store. Live mode merges new posts into the store
        and splices the aggregate tail; --full rebuilds every aggregate over
        the whole build range from raw text.
    * INTERNAL machine (no posts.parquet; Bloomberg Terminal available)
        Holds only ABSTRACTED_DATA - text-free daily aggregates. Live posts
        fold straight into the aggregates. Notebooks 01-07 require raw text
        and therefore never run here; 08-16 run in full.
    Either way the run ends by verifying ABSTRACTED_DATA carries no raw text.

EVERY RUN ALSO PRINTS
    * a DATA COVERAGE table (posts per month, per source) - gaps at a glance
    * a WINDOW CHECK - whether the chosen view window actually has data,
      per source, so an empty chart is never a mystery

NOTEBOOK SAFETY
    1. Before anything runs, every notebook is checked to be valid JSON;
       a truncated notebook is restored from git automatically.
    2. Notebooks execute into a temporary file that is swapped into place
       only after the run finishes and validates - an interrupted run can
       never truncate a notebook.

FLAGS
    --full           rebuild the aggregates over BUILD_START_DATE -> today
                     (run once initially, and after theme/schema changes)
    --fetch          force API fetching in backtest mode
    --skip-fetch     recompute only, no API calls
    --skip-overlays  skip the Bloomberg pull and notebooks 11-16
    --start / --end  override the two dates for this run only
    --external / --internal   force a machine mode instead of auto-detecting
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

try:                     # posts contain emoji/links; avoid cp1252 crashes
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ============================ EDIT THIS =============================
START_DATE = "2018-01-01"    # inclusive, 'YYYY-MM-DD'
END_DATE = "2019-01-01"                # "" = LIVE (to newest); else EXCLUSIVE end e.g. "2021-11-01"
PRICE_TOP_N = 150            # how many top-mentioned tickers the price pull covers

FETCH_LOOKBACK_DAYS = 7      # how far back each live fetch reaches (top posts of
                             # the last N days). Ran late? Set 14-30 to fill the
                             # gap - overlap NEVER duplicates (dedup on post id).
FETCH_MAX_CREDITS = 60       # FetchLayer credit cap PER SOURCE per run (Reddit
                             # and X each). Raise for deeper historical pulls.

BUILD_START_DATE = "2017-01-01"   # the range --full builds the aggregates over.
                             # The aggregates are WINDOW-INDEPENDENT: build them
                             # once over full history, then any START/END view
                             # (live or backtest) renders instantly without a
                             # rebuild. Widen this and rerun --full to extend
                             # history further back (more RAM + time needed).
# ===================================================================
# Every fetched post is stored PERMANENTLY: new posts append into
# posts.parquet (external machine) or fold into ABSTRACTED_DATA (internal),
# both deduped on post id. Weekly runs therefore accumulate into an
# ever-growing database - coverage is limited only by how often the fetch
# runs vs FETCH_LOOKBACK_DAYS, never by the store.

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
SNAP_DIR = os.path.join(ROOT, "data", "processed", "signal_snapshots")

# The FULL chain rebuilds everything from raw text (slow: 01 slices the raw
# store, 06/07 re-score sentiment). Only the external machine can run it.
FULL_CHAIN_NOTEBOOKS = ["01_clean_data", "02_mentions_over_time",
                        "06_ticker_sentiment", "07_theme_sentiment",
                        "08_ticker_conviction", "09_theme_conviction",
                        "10_trading_signals"]
# The SIGNAL notebooks read only the text-free aggregates - fast, and they
# run on both machines. This is all the live fast path recomputes.
SIGNAL_NOTEBOOKS = ["08_ticker_conviction", "09_theme_conviction",
                    "10_trading_signals"]
# Price overlays, ordered mentions -> velocity -> conviction -> signals.
OVERLAY_NOTEBOOKS = ["11_overlay_ticker_mentions",
                     "12_overlay_ticker_first_derivative",
                     "13_overlay_theme_first_derivative",
                     "14_overlay_theme_conviction",
                     "15_overlay_trading_signals",
                     "16_overlay_theme_trading_signals"]
SIGNAL_FILES = ["trade_signals.parquet", "trade_signals_tickers.parquet"]

# Columns that would leak raw posts - the safety check forbids them in the
# committed aggregates.
FORBIDDEN_COLS = {"title", "selftext", "author", "id", "subreddit",
                  "score", "num_comments", "body", "text", "permalink"}
MAX_MB = 25


def log(msg, fh=None):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if fh:
        fh.write(line + "\n")
        fh.flush()


def run(cmd, fh, dry, show=False):
    """Run a child command. show=True streams its output live to the
    terminal (long steps show progress); otherwise output is captured
    quietly. Returns the exit code (0 on --dry-run)."""
    log("RUN  " + " ".join(cmd), fh)
    if dry:
        return 0
    if show:
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            log("FAIL (see the output above)", fh)
        return r.returncode
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log("FAIL " + (r.stderr or r.stdout)[-800:], fh)
    return r.returncode


# ---------------------------------------------------------------------------
# NOTEBOOK SAFETY - validate before running, execute atomically
# ---------------------------------------------------------------------------
def notebook_is_valid(path):
    """A notebook is a JSON file; truncation (interrupted write, cloud-sync
    hiccup) leaves invalid JSON and nbconvert then fails with a confusing
    error. This check is cheap and catches it up front."""
    try:
        with open(path, encoding="utf-8") as f:
            nb = json.load(f)
        return isinstance(nb, dict) and "cells" in nb
    except (ValueError, OSError):
        return False


def check_and_repair_notebooks(fh):
    """Validate every notebook. Broken ones are restored from git (the last
    committed version is always a working one). Returns True if all
    notebooks are valid afterwards."""
    all_ok = True
    for name in sorted(os.listdir(os.path.join(ROOT, "notebooks"))):
        if not name.endswith(".ipynb"):
            continue
        path = os.path.join(ROOT, "notebooks", name)
        if notebook_is_valid(path):
            continue
        log(f"notebook {name} is BROKEN (truncated/invalid JSON) - "
            "restoring the last committed version from git", fh)
        r = subprocess.run(["git", "checkout", "--", f"notebooks/{name}"],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0 and notebook_is_valid(path):
            log(f"  restored {name} from git", fh)
        else:
            log(f"  could NOT restore {name} - fix by hand: "
                f"git checkout -- notebooks/{name}", fh)
            all_ok = False
    return all_ok


def run_notebook(nb, fh, dry, timeout=3600):
    """Execute one notebook ATOMICALLY: run it into a temporary file, check
    the result is valid JSON, and only then swap it over the original. An
    interrupted or failed run leaves the original notebook untouched."""
    py = sys.executable
    src = os.path.join(ROOT, "notebooks", f"{nb}.ipynb")
    tmp_name = f"{nb}.tmp.ipynb"
    tmp = os.path.join(ROOT, "notebooks", tmp_name)

    log(f"running notebook {nb} (output streams below)", fh)
    # "-m nbconvert" (not "-m jupyter nbconvert"): the jupyter launcher needs
    # a jupyter-nbconvert executable on PATH, which per-user pip installs do
    # not add on Windows. Importing the module directly always uses the
    # running interpreter's own nbconvert.
    code = run([py, "-m", "nbconvert", "--to", "notebook",
                "--execute", src,
                "--output", tmp_name, "--output-dir", os.path.join(ROOT, "notebooks"),
                f"--ExecutePreprocessor.timeout={timeout}"], fh, dry, show=True)
    if dry:
        return 0
    if code != 0:
        if os.path.exists(tmp):
            os.remove(tmp)                     # never leave half-written files
        return code
    if not notebook_is_valid(tmp):
        log(f"executed {nb} but the output file is invalid - keeping the "
            "original notebook untouched", fh)
        if os.path.exists(tmp):
            os.remove(tmp)
        return 1
    os.replace(tmp, src)                       # atomic on the same drive
    log(f"ok   {nb}", fh)
    return 0


# ---------------------------------------------------------------------------
# DATA COVERAGE - what the store holds, month by month
# ---------------------------------------------------------------------------
def _compact(n):
    """1234 -> '1.2k', 2500000 -> '2.5M' - keeps the coverage table narrow."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def print_data_coverage(fh, internal):
    """A year x month table of data held, per source, so coverage gaps are
    visible at a glance after every run. The external machine counts POSTS
    from posts.parquet; the internal machine has no raw store, so it counts
    MENTIONS from the committed aggregates (same table shape, same gaps)."""
    import pandas as pd

    if not internal:
        import pyarrow.parquet as pq
        path = os.path.join(ROOT, "data", "processed", "posts.parquet")
        if not os.path.exists(path):
            return
        df = pq.read_table(path, columns=["date", "source"]).to_pandas()
        value_label = "posts"
    else:
        path = os.path.join(ROOT, "data", "processed",
                            "daily_ticker_counts_by_source.parquet")
        if not os.path.exists(path):
            return
        df = pd.read_parquet(path)
        value_label = "ticker mentions"

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month

    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for source in sorted(df["source"].unique()):
        one = df[df["source"] == source]
        if not internal:
            counts = one.groupby(["year", "month"]).size()
        else:
            counts = one.groupby(["year", "month"])["mention_count"].sum()
        log(f"--- DATA COVERAGE: {value_label} per month | source = {source} ---", fh)
        log("  year " + "".join(f"{m:>7}" for m in months), fh)
        for year in sorted(one["year"].unique()):
            cells = []
            for mo in range(1, 13):
                n = counts.get((year, mo), 0)
                cells.append(f"{_compact(n):>7}" if n else f"{'.':>7}")
            log(f"  {year} " + "".join(cells), fh)
    log("('.' = NO data that month - a gap, not a quiet month)", fh)


def check_window_coverage(fh, start, end):
    """Flag up-front whether the chosen VIEW window actually has data, per
    source, so an empty chart is never a mystery. Returns True if at least
    one source covers part of the window."""
    import pandas as pd

    path = os.path.join(ROOT, "data", "processed",
                        "daily_ticker_counts_by_source.parquet")
    if not os.path.exists(path):
        log("window check: no aggregates yet - run 'python update_data.py --full' "
            "once on the external machine to build them", fh)
        return False

    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    agg_lo, agg_hi = df["date"].min(), df["date"].max()
    lo = pd.to_datetime(start)
    hi = pd.to_datetime(end) if end else agg_hi

    log(f"--- WINDOW CHECK: view {lo.date()} -> {hi.date()} | "
        f"aggregates span {agg_lo.date()} -> {agg_hi.date()} ---", fh)
    win = df[(df["date"] >= lo) & (df["date"] <= hi)]
    any_data = False
    for source in sorted(df["source"].unique()):
        one = win[win["source"] == source]
        if len(one):
            any_data = True
            days = one["date"].nunique()
            span_days = max((hi - lo).days, 1)
            pct = min(100, round(100 * days / span_days))
            log(f"  {source:<10} [OK] {one['mention_count'].sum():,} mentions "
                f"over {days} days (~{pct}% of the window)", fh)
        else:
            full = df[df["source"] == source]
            log(f"  {source:<10} [NO DATA in window] this source spans "
                f"{full['date'].min().date()} -> {full['date'].max().date()}", fh)
    if not any_data:
        log("  >>> NO source has data in this window. If the raw store covers "
            "it, run 'python update_data.py --full' (rebuilds the aggregates "
            "over BUILD_START_DATE -> today); otherwise widen the window.", fh)
    return any_data


# ---------------------------------------------------------------------------
# SAFETY CHECK - the committed aggregates must stay text-free
# ---------------------------------------------------------------------------
def verify_abstracted(fh):
    """Confirm ABSTRACTED_DATA is present, small, and carries no columns
    that could reveal raw posts. Reads only each file's schema footer, so
    the check is instant. Returns True if safe to commit."""
    import pyarrow.parquet as pq
    from src import abstracted_data

    log("safety check: ABSTRACTED_DATA is text-free?", fh)
    all_present = all_safe = True
    for name in abstracted_data.FILES:
        path = os.path.join(abstracted_data.ABSTRACTED_DIR, name)
        if not os.path.exists(path):
            log(f"  MISSING  {name}", fh)
            all_present = False
            all_safe = False
            continue
        size_mb = os.path.getsize(path) / (1024 * 1024)
        cols = list(pq.ParquetFile(path).schema_arrow.names)
        bad = [c for c in cols if c.lower() in FORBIDDEN_COLS]
        flag = ""
        if bad:
            flag = f"  <-- LEAK {bad}"
            all_safe = False
        if size_mb > MAX_MB:
            flag += f"  <-- TOO BIG ({size_mb:.1f} MB)"
            all_safe = False
        log(f"  {name:<40} {size_mb:5.2f} MB | {cols}{flag}", fh)

    ok = all_present and all_safe
    log(f"safety check: {'PASS - safe to commit' if ok else 'FAIL - do NOT commit'}", fh)
    return ok


def main():
    p = argparse.ArgumentParser(
        description="Refresh all data for the window set at the top of this file.")
    p.add_argument("--start", default=START_DATE, help="override START_DATE this run")
    p.add_argument("--end", default=END_DATE, help="override END_DATE this run ('' = live)")
    p.add_argument("--full", action="store_true",
                   help="rebuild the aggregates over BUILD_START_DATE -> today")
    p.add_argument("--fetch", action="store_true",
                   help="force API fetching in backtest mode")
    p.add_argument("--skip-fetch", action="store_true",
                   help="recompute only - no API calls")
    p.add_argument("--skip-overlays", action="store_true",
                   help="skip the Bloomberg pull / notebooks 11-16")
    p.add_argument("--external", action="store_true", help="force external-machine mode")
    p.add_argument("--internal", action="store_true", help="force internal-machine mode")
    p.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    args = p.parse_args()
    dry = args.dry_run
    py = sys.executable

    # ---- machine mode: external (has raw store) vs internal ----
    posts_path = os.path.join(ROOT, "data", "processed", "posts.parquet")
    if args.external:
        internal = False
    elif args.internal:
        internal = True
    else:
        internal = not os.path.exists(posts_path)

    # ---- live vs backtest, fast vs full ----
    live = (args.end == "")
    # backtest covers the past, which does not change - no fetching by default
    do_fetch = (live and not args.skip_fetch) or (args.fetch and not args.skip_fetch)
    # The aggregates are window-independent (built over BUILD_START_DATE ->
    # today), so a BACKTEST never rebuilds anything - it only re-renders the
    # overlays for the chosen view window. The full chain runs ONLY with
    # --full (external machine), and always over the whole build range.
    full_chain = args.full and not internal

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(SNAP_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    fh = open(os.path.join(LOG_DIR, f"run_{today}.log"), "a", encoding="utf-8")

    end_label = args.end if args.end else "LIVE (newest)"
    log("=" * 60, fh)
    log("UPDATE DATA", fh)
    log(f"  window : {args.start} -> {end_label}", fh)
    log(f"  machine: {'INTERNAL (abstracted data only)' if internal else 'EXTERNAL (raw store)'} "
        f"(posts.parquet {'present' if os.path.exists(posts_path) else 'absent'})", fh)
    if full_chain:
        path_label = f"FULL rebuild over {BUILD_START_DATE} -> today"
    elif live:
        path_label = "LIVE fast (incremental aggregates + 08/09/10)"
    else:
        path_label = "BACKTEST view (re-render overlays only)"
    log(f"  path   : {path_label}"
        + ("" if not internal else " [internal: 08/09/10 only - 01-07 need raw text]"), fh)
    log(f"  fetch  : {'yes' if do_fetch else 'no (backtest or --skip-fetch)'}", fh)
    log("=" * 60, fh)

    # ---- 0. NOTEBOOK PRE-FLIGHT: validate (and auto-repair) before running ----
    if not dry and not check_and_repair_notebooks(fh):
        log("ABORT: some notebooks are broken and could not be restored", fh)
        return 1

    # ---- 0b. ENVIRONMENT PRE-FLIGHT: every package the run needs must live
    # in THIS interpreter (multiple installed Pythons is the classic cause of
    # intermittent failures). Checking everything up front gives one clear
    # message with one fix command. ----
    if not dry:
        needed = ["pandas", "pyarrow", "matplotlib", "zstandard", "requests",
                  "nbconvert", "ipykernel",          # notebook execution
                  "vaderSentiment", "joblib",        # sentiment (06/07 + live fold)
                  "wordfreq",                        # word-ticker screening (01)
                  "scipy"]                           # inflection peaks (03/05)
        missing = []
        for name in needed:
            try:
                __import__(name)
            except ImportError:
                missing.append(name)
        if missing:
            log(f"ABORT: this python ({py}) is missing: {', '.join(missing)}", fh)
            log(f"fix:  {py} -m pip install {' '.join(missing)} --user", fh)
            log("(or:  pip install -r requirements.txt --user  with the same python)", fh)
            return 1
        # git powers the broken-notebook auto-repair; warn early if absent
        try:
            subprocess.run(["git", "--version"], capture_output=True)
        except FileNotFoundError:
            log("WARNING: git not found on PATH - a truncated notebook cannot "
                "be auto-restored on this machine", fh)

    # The VIEW window travels to the overlays and pull_bloomberg_prices.py
    # through these env vars. The BUILD range for the full chain is set
    # separately just before the chain runs, then restored - the aggregates
    # are always built over the full BUILD_START_DATE -> today range
    # regardless of the view.
    os.environ["PIPELINE_START_DATE"] = args.start
    os.environ["PIPELINE_END_DATE"] = args.end

    # ---- 1. FETCH (raw only; the append steps below own the stores) ----
    if do_fetch:
        run([py, "api_calls/fetch_all.py", "--no-merge",
             "--lookback-days", str(FETCH_LOOKBACK_DAYS),
             "--max-credits", str(FETCH_MAX_CREDITS)], fh, dry, show=True)
    else:
        log("fetch skipped", fh)

    # ---- 2. APPEND into the right store (idempotent either way) ----
    if internal:
        log("folding live raw -> ABSTRACTED_DATA + hydrate (close Jupyter first)", fh)
        run([py, "api_calls/append_live_abstracted.py"], fh, dry, show=True)
    else:
        log("merging live raw -> posts.parquet (close Jupyter first)", fh)
        run([py, "data_ingestion/scripts/merge_live.py"], fh, dry, show=True)
        if live and not full_chain:
            # LIVE FAST PATH: recompute the last ~45 days of the aggregates
            # straight from posts.parquet and splice them onto the untouched
            # history. Same aggregation code as the notebooks, minutes not
            # hours, and always in sync with the raw store.
            #
            # Guard: if the aggregates end long ago, the tail splice would
            # leave a hole in the middle - a full rebuild is required first.
            if not dry:
                import pandas as pd
                agg_path = os.path.join(ROOT, "data", "processed",
                                        "daily_ticker_counts.parquet")
                if os.path.exists(agg_path):
                    newest = pd.to_datetime(
                        pd.read_parquet(agg_path, columns=["date"])["date"]).max()
                    age = (pd.Timestamp.today() - newest).days
                    if age > 90:
                        log(f"ABORT: the aggregates end {newest.date()} ({age} days "
                            "ago). Run 'python update_data.py --full' once to "
                            "restore full history before live fast runs.", fh)
                        return 1
            log("live fast path: refreshing the aggregate tail from posts.parquet", fh)
            code = run([py, "data_ingestion/scripts/refresh_recent_aggregates.py"],
                       fh, dry, show=True)
            if code != 0:
                log("ABORT: aggregate tail refresh failed", fh)
                return 1

    # The internal machine mirrors the latest ABSTRACTED_DATA into
    # data/processed (covers a fresh git pull as well as a local append) so
    # the notebooks never read stale aggregates.
    if internal and not dry:
        from src import abstracted_data
        abstracted_data.hydrate(verbose=False)
        log("hydrated ABSTRACTED_DATA -> data/processed", fh)

    # ---- 2b. DATA COVERAGE + WINDOW CHECK ----
    if not dry:
        print_data_coverage(fh, internal)
        check_window_coverage(fh, args.start, args.end)

    # ---- 3. COMPUTE: notebooks in place ----
    # live -> refresh the signal notebooks; --full -> rebuild everything over
    # the whole build range; backtest -> normally nothing to compute (the
    # aggregates and signals are window-independent) UNLESS the aggregates
    # are newer than the conviction/signal outputs - those are derived
    # locally (they do not travel through git), so after a pull that brings
    # fresh aggregates they must be recomputed once on this machine.
    def signals_stale():
        agg = os.path.join(ROOT, "data", "processed", "daily_ticker_counts.parquet")
        if not os.path.exists(agg):
            return False
        agg_mtime = os.path.getmtime(agg)
        derived = ["daily_ticker_conviction.parquet", "daily_theme_conviction.parquet",
                   "trade_signals.parquet", "trade_signals_tickers.parquet"]
        for f in derived:
            p = os.path.join(ROOT, "data", "processed", f)
            if not os.path.exists(p) or os.path.getmtime(p) < agg_mtime:
                return True
        return False

    if full_chain:
        notebooks = FULL_CHAIN_NOTEBOOKS
        os.environ["PIPELINE_START_DATE"] = BUILD_START_DATE
        os.environ["PIPELINE_END_DATE"] = ""
        log(f"full chain: building aggregates over {BUILD_START_DATE} -> today "
            f"(view window {args.start} -> {end_label} applies to the overlays)", fh)
    elif live:
        notebooks = SIGNAL_NOTEBOOKS
        log("live: refreshing signal notebooks 08/09/10 off the aggregates", fh)
    elif signals_stale():
        notebooks = SIGNAL_NOTEBOOKS
        log("backtest: the aggregates are NEWER than the conviction/signal "
            "outputs (fresh pull?) - running 08/09/10 once to refresh them", fh)
    else:
        notebooks = []
        log(f"backtest: aggregates + signals are up to date - only "
            f"re-rendering the overlays for {args.start} -> {end_label}", fh)
    for nb in notebooks:
        code = run_notebook(nb, fh, dry)
        if code != 0:
            log(f"ABORT: notebook {nb} failed - later steps skipped", fh)
            return 1
        if full_chain and nb == "02_mentions_over_time":
            # theme COUNTS are the one aggregate the notebooks do not build -
            # rebuild them over the same slice notebook 01 just wrote
            log("building theme counts over the full slice", fh)
            code = run([py, "data_ingestion/scripts/build_theme_counts.py"],
                       fh, dry, show=True)
            if code != 0:
                log("ABORT: theme counts build failed", fh)
                return 1
    # restore the VIEW window for the overlays + price pull
    os.environ["PIPELINE_START_DATE"] = args.start
    os.environ["PIPELINE_END_DATE"] = args.end

    # ---- 4. SNAPSHOT the signals (never revised) ----
    import shutil
    for fname in SIGNAL_FILES:
        src_path = os.path.join(ROOT, "data", "processed", fname)
        if os.path.exists(src_path):
            dest = os.path.join(SNAP_DIR, f"{today}_{fname}")
            if not dry and not os.path.exists(dest):
                shutil.copy2(src_path, dest)
            log(f"snapshot -> {dest}", fh)

    # ---- 4b. PRICES + OVERLAYS: pull Bloomberg closes, then render the
    #          overlay notebooks. Non-fatal: without a Terminal/blpapi the
    #          price pull is skipped and so are the overlays. ----
    if not dry and not args.skip_overlays:
        log("pulling Bloomberg prices for the overlays (Terminal must be open)", fh)
        run([py, "pull_bloomberg_prices.py"], fh, dry, show=True)
        prices_path = os.path.join(ROOT, "data", "prices", "prices.parquet")
        if os.path.exists(prices_path):
            for nb in OVERLAY_NOTEBOOKS:
                run_notebook(nb, fh, dry, timeout=1800)
        else:
            log("no data/prices/prices.parquet - skipped overlays 11-16. Open "
                "the Bloomberg Terminal (and pip install blpapi), then re-run.", fh)

    # ---- 5. PUBLISH aggregates to ABSTRACTED_DATA (external machine, in
    #         live or --full runs; a backtest changes nothing to publish) ----
    if not internal and (live or full_chain) and not dry:
        from src import abstracted_data
        log("publishing aggregates -> ABSTRACTED_DATA", fh)
        abstracted_data.export(verbose=False)
    elif not internal and not live:
        log("backtest view: nothing rebuilt, nothing published", fh)

    # ---- 6. SAFETY CHECK the committed data ----
    safe = True
    if not dry:
        safe = verify_abstracted(fh)

    # ---- 7. RUN SUMMARY: the key facts in one glance ----
    if not dry:
        import pandas as pd
        log("", fh)
        log("=" * 60, fh)
        log("RUN SUMMARY", fh)
        log(f"  window        : {args.start} -> {end_label}", fh)
        log(f"  machine/path  : {'INTERNAL' if internal else 'EXTERNAL'} | {path_label}", fh)
        if not internal and os.path.exists(posts_path):
            import pyarrow.parquet as pq
            t = pq.read_table(posts_path, columns=["date", "source"]).to_pandas()
            log(f"  post store    : {len(t):,} posts total", fh)
            for src_name, grp in t.groupby("source"):
                log(f"    {src_name:<10} newest {grp['date'].max()}", fh)
        agg = os.path.join(ROOT, "data", "processed", "daily_ticker_counts.parquet")
        if os.path.exists(agg):
            dates = pd.to_datetime(pd.read_parquet(agg, columns=["date"])["date"])
            log(f"  aggregates    : {dates.min().date()} -> {dates.max().date()}", fh)
        for fname, label in [("trade_signals.parquet", "theme signals"),
                             ("trade_signals_tickers.parquet", "ticker signals")]:
            path_ = os.path.join(ROOT, "data", "processed", fname)
            if os.path.exists(path_):
                s = pd.read_parquet(path_)
                log(f"  {label:<13} : {len(s)} on file", fh)
        prices_path = os.path.join(ROOT, "data", "prices", "prices.parquet")
        log(f"  price overlays: {'rendered (11-16)' if os.path.exists(prices_path) and not args.skip_overlays else 'skipped (no prices.parquet on this machine)'}", fh)
        log(f"  safety check  : {'PASS' if safe else 'FAIL - do NOT commit ABSTRACTED_DATA'}", fh)
        log("=" * 60, fh)
    return 0 if safe else 1


if __name__ == "__main__":
    raise SystemExit(main())

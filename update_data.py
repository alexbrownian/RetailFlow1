#!/usr/bin/env python
"""
update_data.py - THE one file. Edit the window, run it, done.

    python update_data.py

TWO SETTINGS, ONE KNOB (the window at the top of this file):

  LIVE MODE      END_DATE = ""   (the default day-to-day mode)
      Fast path. Fetch a WEEK of the most popular posts from every live
      source (FetchLayer Reddit + X, StockTwits), fold them into the
      aggregates INCREMENTALLY (no slow notebook rebuild), recompute the
      signal notebooks (08/09/10), pull prices and render the overlay
      notebooks (11-14). Minutes, not hours.

  BACKTEST MODE  END_DATE = "YYYY-MM-DD"
      Freeze a past regime. Runs the FULL chain (01-10) from the raw store
      so every aggregate is rebuilt for exactly that window, then renders
      the overlays against prices for the same window. No fetching (the
      past does not change) unless you pass --fetch.

    Examples:
        START_DATE="2021-01-01"; END_DATE="2021-11-01"   -> backtest Jan-Oct 2021
        START_DATE="2021-01-01"; END_DATE=""             -> LIVE, 2021 -> today
        START_DATE="2025-01-01"; END_DATE=""             -> LIVE, 2025 -> today

WORKS ON BOTH MACHINES (auto-detected - you never choose)
    * personal laptop  (data/processed/posts.parquet exists) -> PRODUCER:
        live: merge live posts into posts.parquet AND fold the aggregates
        incrementally; backtest/--full: run the whole notebook chain.
    * work laptop      (no posts.parquet)                     -> CONSUMER:
        fold live posts into GIC_RAW_DATA (text-free), rebuild signals there.
        Notebooks 01-07 need the raw text so they can NEVER run here - only
        08-14 (and that is by design, not a bug).
    Either way it ends by verifying GIC_RAW_DATA carries no raw text.

SAFETY AGAINST TRUNCATED NOTEBOOKS (the old recurring failure)
    1. Before anything runs, every notebook is checked to be valid JSON.
       A broken (usually truncated) notebook is restored from git
       automatically, and the run tells you it did that.
    2. Notebooks are executed to a TEMP file first and only swapped into
       place when the execution finished and produced valid JSON - so an
       interrupted run can never truncate a notebook again.

FLAGS (rarely needed)
    --full         force the full chain (01-10) even in live mode - use after
                   changing the window or when you want sentiment rebuilt
    --fetch        force API fetching in backtest mode
    --skip-fetch   recompute only, no API calls
    --skip-overlays  don't pull Bloomberg prices / render notebooks 11-14
    --start / --end  override the two dates just for this run
    --producer / --consumer   force a mode instead of auto-detecting
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

try:                     # posts contain emoji/links; don't die on cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ============================ EDIT THIS =============================
START_DATE = "2021-01-01"    # inclusive, 'YYYY-MM-DD'
END_DATE = ""                # "" = LIVE (to newest); else EXCLUSIVE end e.g. "2021-11-01"
PRICE_TOP_N = 150            # Bloomberg puller grabs this many top-mentioned tickers
# ===================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
SNAP_DIR = os.path.join(ROOT, "data", "processed", "signal_snapshots")

# The FULL chain rebuilds everything from raw text (slow: 01 slices the 1 GB
# store, 06/07 re-score sentiment). Only the producer can run it.
FULL_CHAIN_NOTEBOOKS = ["01_clean_data", "02_mentions_over_time",
                        "06_ticker_sentiment", "07_theme_sentiment",
                        "08_ticker_conviction", "09_theme_conviction",
                        "10_trading_signals"]
# The SIGNAL notebooks read only the text-free aggregates - fast, and they
# run on both machines. This is all the live fast path recomputes.
SIGNAL_NOTEBOOKS = ["08_ticker_conviction", "09_theme_conviction",
                    "10_trading_signals"]
# Bloomberg price overlays - rendered at the end of every run (need prices).
# Ordered to read naturally: mentions -> velocity -> conviction -> signals,
# tickers before themes.
OVERLAY_NOTEBOOKS = ["11_overlay_ticker_mentions",
                     "12_overlay_ticker_first_derivative",
                     "13_overlay_theme_first_derivative",
                     "14_overlay_theme_conviction",
                     "15_overlay_trading_signals",
                     "16_overlay_theme_trading_signals"]
SIGNAL_FILES = ["trade_signals.parquet", "trade_signals_tickers.parquet"]

# Columns that would leak the raw posts - the safety check forbids them in the
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
    """Run a child command. When show=True its output STREAMS LIVE to the
    terminal, so long steps (the fetch, a notebook) show progress instead of a
    frozen-looking prompt. Otherwise output is captured quietly. Returns the
    exit code (0 on --dry-run)."""
    log("RUN  " + " ".join(cmd), fh)
    if dry:
        return 0
    if show:
        r = subprocess.run(cmd, cwd=ROOT)          # inherit stdout/stderr = live
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
    """A notebook is a JSON file. Truncation (interrupted write, cloud-sync
    hiccup) leaves invalid JSON, and nbconvert then dies with a confusing
    error. This check is cheap and catches it up front."""
    try:
        with open(path, encoding="utf-8") as f:
            nb = json.load(f)
        return isinstance(nb, dict) and "cells" in nb
    except (ValueError, OSError):
        return False


def check_and_repair_notebooks(fh):
    """Validate EVERY notebook. Broken ones are restored from git (the last
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
    """Execute one notebook ATOMICALLY: run it into a temp file, check the
    result is valid JSON, and only then swap it over the original. An
    interrupted or failed run leaves the original notebook untouched."""
    py = sys.executable
    src = os.path.join(ROOT, "notebooks", f"{nb}.ipynb")
    tmp_name = f"{nb}.tmp.ipynb"
    tmp = os.path.join(ROOT, "notebooks", tmp_name)

    log(f"running notebook {nb} (this can take a while; output streams below)", fh)
    # NOTE: "-m nbconvert" (not "-m jupyter nbconvert"). The jupyter launcher
    # looks for a jupyter-nbconvert.exe on PATH, which a --user pip install
    # doesn't add on Windows -> "Jupyter command not found". Importing the
    # module directly always uses THIS python's own nbconvert.
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


def _compact(n):
    """1234 -> '1.2k', 2500000 -> '2.5M' - keeps the coverage table narrow."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def print_data_coverage(fh, consumer):
    """A year x month table of how much data we hold, per source - so gaps
    (like the X hole across 2021-2023) are visible at a glance after every
    run. Producer counts POSTS from posts.parquet; the work laptop has no
    raw store, so it counts MENTIONS from the committed aggregates instead
    (same shape of table, same gaps)."""
    import pandas as pd

    if not consumer:
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
        if not consumer:
            counts = one.groupby(["year", "month"]).size()
        else:
            counts = one.groupby(["year", "month"])["mention_count"].sum()
        log(f"--- DATA COVERAGE: {value_label} per month | source = {source} ---", fh)
        header = "  year " + "".join(f"{m:>7}" for m in months)
        log(header, fh)
        for year in sorted(one["year"].unique()):
            cells = []
            for mo in range(1, 13):
                n = counts.get((year, mo), 0)
                cells.append(f"{_compact(n):>7}" if n else f"{'.':>7}")
            log(f"  {year} " + "".join(cells), fh)
    log("('.' = NO data that month - that is a gap, not a quiet month)", fh)


def verify_gic(fh):
    """Confirm GIC_RAW_DATA is present, small, and carries NO raw-revealing
    columns. Returns True if safe. (This is the old verify_gic.py, inlined.)"""
    import pandas as pd
    from src import gic_data

    log("safety check: GIC_RAW_DATA is text-free?", fh)
    all_present = all_safe = True
    for name in gic_data.FILES:
        path = os.path.join(gic_data.GIC_DIR, name)
        if not os.path.exists(path):
            log(f"  MISSING  {name}", fh)
            all_present = False
            all_safe = False
            continue
        size_mb = os.path.getsize(path) / (1024 * 1024)
        cols = list(pd.read_parquet(path).columns)
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
                   help="run the FULL chain (01-10) even in live mode")
    p.add_argument("--fetch", action="store_true",
                   help="force API fetching in backtest mode")
    p.add_argument("--skip-fetch", action="store_true",
                   help="recompute only - no API calls")
    p.add_argument("--skip-overlays", action="store_true",
                   help="don't pull Bloomberg prices / render notebooks 11-14")
    p.add_argument("--producer", action="store_true", help="force producer mode")
    p.add_argument("--consumer", action="store_true", help="force consumer mode")
    p.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    args = p.parse_args()
    dry = args.dry_run
    py = sys.executable

    # ---- mode: producer (has raw store) vs consumer (work laptop) ----
    posts_path = os.path.join(ROOT, "data", "processed", "posts.parquet")
    if args.producer:
        consumer = False
    elif args.consumer:
        consumer = True
    else:
        consumer = not os.path.exists(posts_path)

    # ---- live vs backtest, fast vs full ----
    live = (args.end == "")
    # backtest = the past; it does not change, so default to NO fetching there.
    do_fetch = (live and not args.skip_fetch) or (args.fetch and not args.skip_fetch)
    # the consumer can never run 01-07 (no raw text on the work laptop) -
    # asking for --full there still runs just 08/09/10 (the banner says so).
    full_chain = (args.full or not live) and not consumer

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(SNAP_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    fh = open(os.path.join(LOG_DIR, f"run_{today}.log"), "a", encoding="utf-8")

    end_label = args.end if args.end else "LIVE (newest)"
    log("=" * 60, fh)
    log("UPDATE DATA", fh)
    log(f"  window : {args.start} -> {end_label}", fh)
    log(f"  mode   : {'CONSUMER (work laptop)' if consumer else 'PRODUCER'} "
        f"(posts.parquet {'present' if os.path.exists(posts_path) else 'absent'})", fh)
    log(f"  path   : {'LIVE fast (incremental aggregates + 08/09/10)' if (live and not full_chain) else 'FULL chain (01-10 rebuild)'}"
        + ("" if not consumer else " [consumer: 08/09/10 only - 01-07 need raw text]"), fh)
    log(f"  fetch  : {'yes' if do_fetch else 'no (backtest or --skip-fetch)'}", fh)
    log("=" * 60, fh)

    # ---- 0. NOTEBOOK PRE-FLIGHT: validate (and auto-repair) before running ----
    if not dry and not check_and_repair_notebooks(fh):
        log("ABORT: some notebooks are broken and could not be restored", fh)
        return 1

    # ---- 0b. ENVIRONMENT PRE-FLIGHT: every package the run will need must
    # live in THIS python (the one running this script - multiple installed
    # Pythons is the classic cause of "works one day, not the next").
    # Checking everything up front gives ONE clear message with ONE fix
    # command, instead of a confusing failure 20 minutes into the run. ----
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
        # git powers the broken-notebook auto-repair; warn early if absent.
        try:
            subprocess.run(["git", "--version"], capture_output=True)
        except FileNotFoundError:
            log("WARNING: git not found on PATH - a truncated notebook cannot "
                "be auto-restored on this machine", fh)

    # The window travels to every child process (notebooks 01 and 11-14, and
    # pull_bloomberg_prices.py) through these two env vars, so --start/--end
    # overrides reach everything.
    os.environ["PIPELINE_START_DATE"] = args.start
    os.environ["PIPELINE_END_DATE"] = args.end

    # ---- 1. FETCH (raw only; the append steps below own the stores) ----
    if do_fetch:
        run([py, "api_calls/fetch_all.py", "--no-merge"], fh, dry, show=True)
    else:
        log("fetch skipped", fh)

    # ---- 2. APPEND into the right store(s) (idempotent either way) ----
    if consumer:
        log("folding live raw -> GIC_RAW_DATA + hydrate (close Jupyter first!)", fh)
        run([py, "api_calls/append_live_to_gic.py"], fh, dry, show=True)
    else:
        log("merging live raw -> posts.parquet (close Jupyter first!)", fh)
        run([py, "data_ingestion/scripts/merge_live.py"], fh, dry, show=True)
        if live and not full_chain:
            # LIVE FAST PATH: recompute the last ~45 days of the aggregates
            # straight from posts.parquet and splice them onto the untouched
            # history. Same aggregation code as the notebooks, minutes not
            # hours, and always in sync with the raw store.
            log("live fast path: refreshing the aggregate tail from posts.parquet", fh)
            code = run([py, "data_ingestion/scripts/refresh_recent_aggregates.py"],
                       fh, dry, show=True)
            if code != 0:
                log("ABORT: aggregate tail refresh failed", fh)
                return 1

    # On the work laptop make sure data/processed reflects the latest
    # GIC_RAW_DATA (covers a fresh `git pull` as well as a local append) so the
    # consumer notebooks never read stale aggregates.
    if consumer and not dry:
        from src import gic_data
        gic_data.hydrate(verbose=False)
        log("hydrated GIC_RAW_DATA -> data/processed", fh)

    # ---- 2b. DATA COVERAGE: what do we actually hold, month by month? ----
    if not dry:
        print_data_coverage(fh, consumer)

    # ---- 3. COMPUTE: notebooks in place ----
    notebooks = FULL_CHAIN_NOTEBOOKS if full_chain else SIGNAL_NOTEBOOKS
    if full_chain:
        log(f"notebook window: START={args.start}  END={end_label}", fh)
    else:
        log(f"fast path: notebooks 08/09/10 off the aggregates "
            f"(window {args.start} -> {end_label})", fh)
    for nb in notebooks:
        code = run_notebook(nb, fh, dry)
        if code != 0:
            log(f"ABORT: notebook {nb} failed - later steps skipped", fh)
            return 1

    # ---- 4. SNAPSHOT the signals (never revised) ----
    import shutil
    for fname in SIGNAL_FILES:
        src_path = os.path.join(ROOT, "data", "processed", fname)
        if os.path.exists(src_path):
            dest = os.path.join(SNAP_DIR, f"{today}_{fname}")
            if not dry and not os.path.exists(dest):
                shutil.copy2(src_path, dest)
            log(f"snapshot -> {dest}", fh)

    # ---- 4b. PRICES + OVERLAYS: pull Bloomberg, then render notebooks 11-14 so
    #          this one command fills in the overlay plots. Non-fatal: if the
    #          price pull can't run (no Terminal/blpapi) we just skip 11-14. ----
    if not dry and not args.skip_overlays:
        log("pulling Bloomberg prices for the overlays (Terminal must be open)", fh)
        run([py, "pull_bloomberg_prices.py"], fh, dry, show=True)
        prices_path = os.path.join(ROOT, "data", "prices", "prices.parquet")
        if os.path.exists(prices_path):
            for nb in OVERLAY_NOTEBOOKS:
                run_notebook(nb, fh, dry, timeout=1800)
        else:
            log("no data/prices/prices.parquet - skipped overlays 11-14. Open the "
                "Bloomberg Terminal (and pip install blpapi), then re-run.", fh)

    # ---- 5. PUBLISH aggregates to GIC_RAW_DATA (producer only; the fast path
    #         spliced data/processed, so exporting keeps GIC in step with it.
    #         The consumer's append step already wrote GIC directly.) ----
    if not consumer and not dry:
        from src import gic_data
        log("publishing aggregates -> GIC_RAW_DATA", fh)
        gic_data.export(verbose=False)

    # ---- 6. SAFETY CHECK the committed data ----
    safe = True
    if not dry:
        safe = verify_gic(fh)

    log("=== update_data finished ===", fh)
    if not consumer:
        log("next: git add GIC_RAW_DATA && git commit && git push", fh)
    else:
        log("next (if safe): git add GIC_RAW_DATA && git commit && git push", fh)
    return 0 if safe else 1


if __name__ == "__main__":
    raise SystemExit(main())

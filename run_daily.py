# run_daily.py
# ============
# THE ORCHESTRATOR: one command that pulls fresh data, pushes it through
# the whole model, snapshots the signals, and leaves the dashboard with
# new numbers. This is what the Task Scheduler job (or the dashboard's
# refresh button) runs every day.
#
#   python run_daily.py                     # full run
#   python run_daily.py --start-date 2022-01-01   # slice from a different depth
#   python run_daily.py --fetch-only   # FETCH LAYER ONLY: call every source
#                                      #   that has credentials, skip the rest
#                                      #   gracefully, write raw files, stop
#   python run_daily.py --dry-run      # print the plan, execute nothing
#   python run_daily.py --skip-fetch   # recompute only (no API calls)
#   python run_daily.py --consumer     # WORK LAPTOP: fetch -> fold into
#                                      #   GIC_RAW_DATA -> hydrate -> run the
#                                      #   consumer notebooks (08/09/10). No raw
#                                      #   posts.parquet needed.
#
# WHAT IT DOES, IN ORDER:
#   1. FETCH  - runs api_calls/fetch_all.py, THE one file for API calls:
#               it checks .env first and calls only the sources whose keys
#               are filled (empty key = ignored entirely). You can also run
#               it directly:  python api_calls/fetch_all.py
#   2. MERGE  - merge_live.py APPENDS every new live post (Reddit, X and
#               StockTwits raw) into posts.parquet ("first seen wins",
#               idempotent). This is the step that makes live data reach the
#               signals. (add_x_data.py is separate - it rebuilds the X block
#               from the frozen HuggingFace dumps, for the historical backfill.)
#   3. COMPUTE - executes the notebooks IN PLACE, in chain order:
#                 01 (slice+screening) -> 02 (counts) -> 06 (ticker sent)
#                 -> 07 (theme sent) -> 09 (BUY/SELL signals)
#               The notebooks ARE the pipeline - one source of truth, and
#               after each run they hold fresh outputs you can open.
#               The window is passed to notebook 01 automatically via env
#               vars (START_DATE = --start-date, END_DATE = open), so the
#               window always extends to the newest data - no notebook edit.
#   4. SNAPSHOT - copies trade_signals*.parquet to
#                 data/processed/signal_snapshots/<date>_*.parquet.
#               Snapshots are NEVER revised - they are the point-in-time
#               record the backtest-to-live comparison depends on
#               (README live-data checklist).
#
# Caches take care of themselves: the sentiment cache invalidates when the
# slice row-count changes, so only runs with new data pay the scoring cost.
# A full run with new data is roughly 5-15 minutes on a normal machine.

import argparse
import datetime
import os
import shutil
import subprocess
import sys

try:                     # posts contain emoji/links; don't die on cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
SNAP_DIR = os.path.join(ROOT, "data", "processed", "signal_snapshots")
# PRODUCER chain (needs the raw posts.parquet): text -> numbers -> signals.
NOTEBOOKS = ["01_clean_data", "02_mentions_over_time", "06_ticker_sentiment",
             "07_theme_sentiment", "10_trading_signals"]
# CONSUMER chain (work laptop, --consumer): reads ONLY the text-free aggregates
# in data/processed (put there by hydrate), so it needs no raw posts. Rebuilds
# conviction + signals from the freshly appended GIC_RAW_DATA.
CONSUMER_NOTEBOOKS = ["08_ticker_conviction", "09_theme_conviction",
                      "10_trading_signals"]
SIGNAL_FILES = ["trade_signals.parquet", "trade_signals_tickers.parquet"]

# The history depth notebook 01 slices from. END is always OPEN for live
# runs, so the window auto-extends to the newest post each day. Override the
# start with --start-date. Notebook 01 reads these two via PIPELINE_START_DATE
# / PIPELINE_END_DATE (empty END = open-ended).
DEFAULT_START_DATE = "2023-10-01"


def log(msg, fh=None):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if fh:
        fh.write(line + "\n"); fh.flush()


def run(cmd, fh, dry, show=False):
    log("RUN  " + " ".join(cmd), fh)
    if dry:
        return 0
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if show and r.stdout:                       # echo the child's own output
        for line in r.stdout.strip().splitlines():
            log("  | " + line, fh)
    if r.returncode != 0:
        log("FAIL " + (r.stderr or r.stdout)[-800:], fh)
    return r.returncode


def main():
    p = argparse.ArgumentParser(description="Daily pipeline run")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-fetch", action="store_true")
    p.add_argument("--fetch-only", action="store_true",
                   help="run the fetch layer only (each source self-skips "
                        "if its key is missing), then stop")
    p.add_argument("--consumer", action="store_true",
                   help="WORK-LAPTOP mode: fetch -> fold into GIC_RAW_DATA -> "
                        "hydrate -> run the CONSUMER notebooks (08/09/10) only. "
                        "No raw posts.parquet needed.")
    p.add_argument("--start-date", default=DEFAULT_START_DATE,
                   help=f"history depth notebook 01 slices from (default "
                        f"{DEFAULT_START_DATE}); the END is always open so the "
                        f"window extends to the newest data")
    args = p.parse_args()
    dry = args.dry_run

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(SNAP_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    fh = open(os.path.join(LOG_DIR, f"run_{today}.log"), "a", encoding="utf-8")
    log("=== daily run started ===", fh)

    py = sys.executable

    # ---- 1. FETCH: one file owns it all (key check + calls). --no-merge:
    #         write raw only; step 2 below owns the single parquet append. ----
    if not args.skip_fetch:
        run([py, "api_calls/fetch_all.py", "--no-merge"], fh, dry, show=True)
    else:
        log("fetch skipped (--skip-fetch)", fh)

    if args.fetch_only:
        log("=== fetch-only run finished (no merge/compute/snapshot) - "
            "run check_live_ingestion.py to see what landed ===", fh)
        return 0

    # ---- 2. MERGE / APPEND: fold EVERY live source (Reddit / X / StockTwits)
    #         into the right store. Append-only + idempotent either way, so it's
    #         safe to always run - nothing new means nothing appended. ----
    if args.consumer:
        # Work laptop: no raw store. Fold into GIC_RAW_DATA (text-free) and
        # hydrate GIC_RAW_DATA -> data/processed so the consumer notebooks find
        # fresh aggregates. append_live_to_gic.py does both.
        log("folding live raw -> GIC_RAW_DATA + hydrate (close Jupyter first!)", fh)
        if run([py, "api_calls/append_live_to_gic.py"], fh, dry, show=True) != 0:
            log("gic append failed - continuing with the existing aggregates", fh)
    else:
        log("merging live raw -> posts.parquet (close Jupyter first!)", fh)
        if run([py, "data_ingestion/scripts/merge_live.py"], fh, dry, show=True) != 0:
            log("merge failed - continuing with the existing parquet", fh)

    # ---- 3. COMPUTE: execute the notebook chain in place ----
    # Producer runs the full chain from raw; consumer runs only 08/09/10 off the
    # aggregates (no raw needed, so no window to set).
    notebooks = CONSUMER_NOTEBOOKS if args.consumer else NOTEBOOKS
    if not args.consumer:
        # Notebook 01 reads these; END empty = open-ended so the window extends
        # to the newest post. Child nbconvert processes inherit this environment.
        os.environ["PIPELINE_START_DATE"] = args.start_date
        os.environ["PIPELINE_END_DATE"] = ""   # empty => None => window stays open
        log(f"notebook window: START_DATE={args.start_date}  END_DATE=open (extends to newest)", fh)
    else:
        log("consumer mode: running 08/09/10 off GIC aggregates (no raw, no window)", fh)
    for nb in notebooks:
        code = run([py, "-m", "jupyter", "nbconvert", "--to", "notebook",
                    "--execute", "--inplace",
                    f"notebooks/{nb}.ipynb",
                    "--ExecutePreprocessor.timeout=3600"], fh, dry)
        if code != 0:
            log(f"ABORT: notebook {nb} failed - see log; later steps skipped", fh)
            return 1
        log(f"ok   {nb}", fh)

    # ---- 4. SNAPSHOT the signals (never revised) ----
    for fname in SIGNAL_FILES:
        src_path = os.path.join(ROOT, "data", "processed", fname)
        if os.path.exists(src_path):
            dest = os.path.join(SNAP_DIR, f"{today}_{fname}")
            if not dry and not os.path.exists(dest):   # never overwrite a snapshot
                shutil.copy2(src_path, dest)
            log(f"snapshot -> {dest}", fh)

    log("=== daily run finished - open the dashboard ===", fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# run_daily.py
# ============
# THE ORCHESTRATOR: one command that pulls fresh data, pushes it through
# the whole model, snapshots the signals, and leaves the dashboard with
# new numbers. This is what the Task Scheduler job (or the dashboard's
# refresh button) runs every day.
#
#   python run_daily.py                # full run
#   python run_daily.py --fetch-only   # FETCH LAYER ONLY: call every source
#                                      #   that has credentials, skip the rest
#                                      #   gracefully, write raw files, stop
#   python run_daily.py --dry-run      # print the plan, execute nothing
#   python run_daily.py --skip-fetch   # recompute only (no API calls)
#
# WHAT IT DOES, IN ORDER:
#   1. FETCH  - runs api_calls/fetch_all.py, THE one file for API calls:
#               it checks .env first and calls only the sources whose keys
#               are filled (empty key = ignored entirely). You can also run
#               it directly:  python api_calls/fetch_all.py
#   2. MERGE  - add_x_data.py rebuilds the X block of posts.parquet if the
#               live X raw file changed since the parquet was written.
#   3. COMPUTE - executes the notebooks IN PLACE, in chain order:
#                 01 (slice+screening) -> 02 (counts) -> 06 (ticker sent)
#                 -> 07 (theme sent) -> 09 (BUY/SELL signals)
#               The notebooks ARE the pipeline - one source of truth, and
#               after each run they hold fresh outputs you can open.
#               IMPORTANT one-time setup: in notebook 01 set END_DATE=None
#               so the window automatically extends as new data arrives.
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

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
SNAP_DIR = os.path.join(ROOT, "data", "processed", "signal_snapshots")
NOTEBOOKS = ["01_clean_data", "02_mentions_over_time", "06_ticker_sentiment",
             "07_theme_sentiment", "10_trading_signals"]
SIGNAL_FILES = ["trade_signals.parquet", "trade_signals_tickers.parquet"]


def log(msg, fh=None):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if fh:
        fh.write(line + "\n"); fh.flush()


def run(cmd, fh, dry, show=False):
    log("RUN  " + " ".join(cmd), fh)
    if dry:
        return 0
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
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
    args = p.parse_args()
    dry = args.dry_run

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(SNAP_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    fh = open(os.path.join(LOG_DIR, f"run_{today}.log"), "a", encoding="utf-8")
    log("=== daily run started ===", fh)

    py = sys.executable

    # ---- 1. FETCH: one file owns it all (key check + calls) ----
    if not args.skip_fetch:
        run([py, "api_calls/fetch_all.py"], fh, dry, show=True)
    else:
        log("fetch skipped (--skip-fetch)", fh)

    if args.fetch_only:
        log("=== fetch-only run finished (no merge/compute/snapshot) - "
            "run check_live_ingestion.py to see what landed ===", fh)
        return 0

    # ---- 2. MERGE (only if live X raw is newer than the parquet) ----
    posts = os.path.join(ROOT, "data", "processed", "posts.parquet")
    x_live = os.path.join(ROOT, "data", "raw", "X Data", "x_api_live.csv.zst")
    if os.path.exists(x_live) and os.path.exists(posts) \
            and os.path.getmtime(x_live) > os.path.getmtime(posts):
        log("live X raw changed -> rebuilding X block (close Jupyter first!)", fh)
        if run([py, "data_ingestion/scripts/add_x_data.py"], fh, dry) != 0:
            log("merge failed - continuing with the existing parquet", fh)
    else:
        log("no new X raw - merge skipped", fh)

    # ---- 3. COMPUTE: execute the notebook chain in place ----
    for nb in NOTEBOOKS:
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

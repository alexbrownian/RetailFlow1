#!/usr/bin/env python
"""
update_data.py - THE one file. Edit the window, run it, done.

    python update_data.py

It is the WHOLE pipeline in one place: fetch every live source, fold the new
posts into the right store, recompute the notebooks, snapshot the signals,
publish the committable aggregates, and safety-check them. It replaces the old
run_daily.py + verify_gic.py (both removed - this file does their jobs).

THE WINDOW (the only thing you normally edit)
    START_DATE  first day, 'YYYY-MM-DD' inclusive.
    END_DATE    last day, 'YYYY-MM-DD' EXCLUSIVE.
                "" = LIVE (extends to the newest data / today).
                a date = freeze a past regime for backtesting.
    Examples:
        START_DATE="2021-01-01"; END_DATE="2021-11-01"   -> Jan-Oct 2021
        START_DATE="2021-01-01"; END_DATE=""             -> 2021 -> today (LIVE)
        START_DATE="2025-01-01"; END_DATE=""             -> 2025 -> today (LIVE)

WORKS ON BOTH MACHINES (auto-detected - you never choose)
    * personal laptop  (data/processed/posts.parquet exists) -> PRODUCER:
        append live posts into posts.parquet, run the full notebook chain,
        publish the aggregates into GIC_RAW_DATA.
    * work laptop      (no posts.parquet)                     -> CONSUMER:
        fold live posts into GIC_RAW_DATA (text-free), rebuild signals there.
    Either way it ends by verifying GIC_RAW_DATA carries no raw text.

FLAGS (rarely needed)
    --skip-fetch   recompute only, no API calls (handy after changing the window)
    --start / --end   override the two dates just for this run
    --producer / --consumer   force a mode instead of auto-detecting
"""

import argparse
import datetime
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
PRICE_TOP_N = 50             # Bloomberg puller grabs this many top-mentioned tickers
# ===================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
SNAP_DIR = os.path.join(ROOT, "data", "processed", "signal_snapshots")

# PRODUCER runs the full chain from raw text; CONSUMER runs only the notebooks
# that read the text-free aggregates.
PRODUCER_NOTEBOOKS = ["01_clean_data", "02_mentions_over_time",
                      "06_ticker_sentiment", "07_theme_sentiment",
                      "08_ticker_conviction", "09_theme_conviction",
                      "10_trading_signals"]
CONSUMER_NOTEBOOKS = ["08_ticker_conviction", "09_theme_conviction",
                      "10_trading_signals"]
# Bloomberg price overlays - rendered at the end of every run (need prices).
OVERLAY_NOTEBOOKS = ["11_overlay_ticker_mentions",
                     "12_overlay_ticker_first_derivative",
                     "13_overlay_theme_conviction",
                     "14_overlay_trading_signals"]
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
    log("=" * 60, fh)

    # ---- 1. FETCH (raw only; the append step below owns the store) ----
    if not args.skip_fetch:
        run([py, "api_calls/fetch_all.py", "--no-merge"], fh, dry, show=True)
    else:
        log("fetch skipped (--skip-fetch)", fh)

    # ---- 2. APPEND into the right store (idempotent either way) ----
    if consumer:
        log("folding live raw -> GIC_RAW_DATA + hydrate (close Jupyter first!)", fh)
        run([py, "api_calls/append_live_to_gic.py"], fh, dry, show=True)
    else:
        log("merging live raw -> posts.parquet (close Jupyter first!)", fh)
        run([py, "data_ingestion/scripts/merge_live.py"], fh, dry, show=True)

    # On the work laptop make sure data/processed reflects the latest
    # GIC_RAW_DATA (covers a fresh `git pull` as well as a local append) so the
    # consumer notebooks never read stale aggregates.
    if consumer and not dry:
        from src import gic_data
        gic_data.hydrate(verbose=False)
        log("hydrated GIC_RAW_DATA -> data/processed", fh)

    # ---- 3. COMPUTE: notebooks in place ----
    notebooks = CONSUMER_NOTEBOOKS if consumer else PRODUCER_NOTEBOOKS
    if not consumer:
        # notebook 01 reads the window; empty END => open-ended (live)
        os.environ["PIPELINE_START_DATE"] = args.start
        os.environ["PIPELINE_END_DATE"] = args.end
        log(f"notebook window: START={args.start}  END={end_label}", fh)
    else:
        log(f"consumer: notebooks 08/09/10 off GIC aggregates "
            f"(window {args.start} -> {end_label} already in the aggregates)", fh)
    for nb in notebooks:
        log(f"running notebook {nb} (this can take a while; output streams below)", fh)
        code = run([py, "-m", "jupyter", "nbconvert", "--to", "notebook",
                    "--execute", "--inplace", f"notebooks/{nb}.ipynb",
                    "--ExecutePreprocessor.timeout=3600"], fh, dry, show=True)
        if code != 0:
            log(f"ABORT: notebook {nb} failed - later steps skipped", fh)
            return 1
        log(f"ok   {nb}", fh)

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
                log(f"rendering overlay {nb} (output streams below)", fh)
                run([py, "-m", "jupyter", "nbconvert", "--to", "notebook",
                     "--execute", "--inplace", f"notebooks/{nb}.ipynb",
                     "--ExecutePreprocessor.timeout=1800"], fh, dry, show=True)
        else:
            log("no data/prices/prices.parquet - skipped overlays 11-14. Open the "
                "Bloomberg Terminal (and pip install blpapi), then re-run.", fh)

    # ---- 5. PUBLISH aggregates to GIC_RAW_DATA (producer only; the consumer's
    #         append step already wrote GIC_RAW_DATA + hydrated) ----
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

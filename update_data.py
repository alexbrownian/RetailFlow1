#!/usr/bin/env python
"""
update_data.py - THE one file to edit and run to refresh everything.

EDIT the window in the CONFIG block below, then run:

    python update_data.py

WHAT THE WINDOW MEANS
    START_DATE  first day to include - 'YYYY-MM-DD', inclusive.
    END_DATE    last day - 'YYYY-MM-DD', EXCLUSIVE.
                Leave it "" for LIVE: the window extends to the newest data
                (today for live sources). Set a date to freeze a past regime.

    Examples (just edit the two lines):
        START_DATE = "2021-01-01" ; END_DATE = "2021-11-01"   -> Jan-Oct 2021
        START_DATE = "2021-01-01" ; END_DATE = ""             -> 2021 -> today (LIVE)
        START_DATE = "2025-01-01" ; END_DATE = ""             -> 2025 -> today (LIVE)

WHAT IT DOES
    Runs the full pipeline for that window - fetch every live source (Reddit,
    StockTwits, and X if a key is set; no 1-credit test cap, a real refresh),
    fold the new posts into the right store, recompute the notebooks, snapshot
    the signals. It works on BOTH machines because run_daily.py auto-detects:
        * personal laptop (data/processed/posts.parquet exists)
              -> appends live posts into posts.parquet, runs the full chain
        * work laptop (no posts.parquet)
              -> folds live posts into GIC_RAW_DATA (text-free), rebuilds
                 signals from there
    So the SAME command does the right thing in both places - you never pick.

    (Prices for the Bloomberg overlays are pulled separately, on the work
    laptop, by pull_bloomberg_prices.py - it reads PRICE_TOP_N and the window
    from this same file, so there is still only ONE place to edit.)
"""

import argparse
import os
import subprocess
import sys

# ============================ EDIT THIS =============================
START_DATE = "2021-01-01"    # inclusive, 'YYYY-MM-DD'
END_DATE = ""                # "" = LIVE (to newest); else EXCLUSIVE end e.g. "2021-11-01"
PRICE_TOP_N = 50             # Bloomberg puller grabs this many top-mentioned tickers
# ===================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    p = argparse.ArgumentParser(
        description="Refresh all data for the window set at the top of this file.")
    p.add_argument("--start", default=START_DATE,
                   help="override START_DATE just for this run")
    p.add_argument("--end", default=END_DATE,
                   help="override END_DATE just for this run ('' = live)")
    p.add_argument("--skip-fetch", action="store_true",
                   help="recompute only - no API calls (useful after editing the window)")
    args = p.parse_args()

    print("=" * 64)
    print("UPDATE DATA")
    print(f"  window : {args.start}  ->  {args.end or 'LIVE (newest)'}")
    print(f"  machine: auto (run_daily picks producer vs work-laptop)")
    print("=" * 64)

    # One orchestrator owns fetch + append + compute + snapshot; we just hand it
    # the window. run_daily.py decides producer vs consumer by itself.
    cmd = [sys.executable, os.path.join(ROOT, "run_daily.py"),
           "--start-date", args.start, "--end-date", args.end]
    if args.skip_fetch:
        cmd.append("--skip-fetch")

    rc = subprocess.run(cmd, cwd=ROOT).returncode
    if rc == 0:
        print("\nupdate_data: done. On the work laptop, remember to "
              "`git add GIC_RAW_DATA && git commit && git push`.")
    else:
        print(f"\nupdate_data: run_daily exited {rc} - see the log in logs/.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

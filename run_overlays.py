#!/usr/bin/env python
"""
run_overlays.py - build ONLY the Bloomberg price overlays (notebooks 11-14).

Use this when you don't want the whole live pipeline. It assumes the aggregates
are already in data/processed (from update_data.py, a git pull + hydrate, or a
manual run of notebooks 08/09/10) and just refreshes the price overlays.

    python run_overlays.py               # hydrate -> pull prices -> run 11-14
    python run_overlays.py --no-prices   # skip the Bloomberg pull (prices already pulled)
    python run_overlays.py --no-hydrate  # skip the GIC_RAW_DATA -> data/processed copy
    python run_overlays.py --only 13     # run just one overlay (e.g. 13)

Unlike update_data.py, every step's output STREAMS LIVE to the terminal, so you
see progress instead of a frozen-looking prompt.
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROC = os.path.join(ROOT, "data", "processed")
PRICES = os.path.join(ROOT, "data", "prices", "prices.parquet")

# each overlay + the data/processed file it needs (besides prices.parquet).
# 11/12 need only the committed counts (present after hydrate); 13 needs the
# theme conviction from notebook 09; 14 needs the signals from notebook 10.
OVERLAYS = [
    ("11_overlay_ticker_mentions",         "daily_ticker_counts.parquet",   "hydrate"),
    ("12_overlay_ticker_first_derivative", "daily_ticker_counts.parquet",   "hydrate"),
    ("13_overlay_theme_conviction",        "daily_theme_conviction.parquet", "notebook 09"),
    ("14_overlay_trading_signals",         "trade_signals_tickers.parquet", "notebook 10"),
]


def stream(cmd):
    """Run a command with its output streaming LIVE (inherits the terminal)."""
    print(">>>", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


def main():
    p = argparse.ArgumentParser(description="Run the price-overlay notebooks 11-14.")
    p.add_argument("--no-prices", action="store_true", help="skip pull_bloomberg_prices.py")
    p.add_argument("--no-hydrate", action="store_true", help="skip GIC_RAW_DATA -> data/processed")
    p.add_argument("--only", help="run just one overlay, e.g. --only 13")
    args = p.parse_args()
    py = sys.executable

    # ---- 1. hydrate so data/processed has the latest committed aggregates ----
    if not args.no_hydrate:
        print("[1/3] hydrate GIC_RAW_DATA -> data/processed", flush=True)
        from src import gic_data
        gic_data.hydrate()

    # ---- 2. prices (the input the overlays draw against) ----
    if not args.no_prices:
        print("\n[2/3] pull Bloomberg prices (the Terminal must be open)", flush=True)
        if stream([py, "pull_bloomberg_prices.py"]) != 0:
            print("price pull failed. Fix the Terminal/blpapi, or pass --no-prices "
                  "if data/prices/prices.parquet already exists.")
            return 1
    else:
        print("\n[2/3] skipping price pull (--no-prices)", flush=True)

    if not os.path.exists(PRICES):
        print(f"no prices at {PRICES} - run without --no-prices first.")
        return 1

    # ---- 3. run the overlay notebooks (live output) ----
    chosen = [row for row in OVERLAYS if (args.only is None or row[0].startswith(args.only))]
    if not chosen:
        print("no overlay matches", args.only)
        return 1

    print(f"\n[3/3] running {len(chosen)} overlay notebook(s)", flush=True)
    failed = []
    for name, needs, made_by in chosen:
        if not os.path.exists(os.path.join(PROC, needs)):
            print(f"  skip {name} - needs data/processed/{needs} "
                  f"(produce it via {made_by} first)")
            continue
        print(f"\n--- executing notebooks/{name}.ipynb ---", flush=True)
        code = stream([py, "-m", "jupyter", "nbconvert", "--to", "notebook",
                       "--execute", "--inplace", f"notebooks/{name}.ipynb",
                       "--ExecutePreprocessor.timeout=1800"])
        if code != 0:
            print(f"  {name} FAILED - open it in Jupyter to see the error")
            failed.append(name)
        else:
            print(f"  done {name}")

    print("\noverlays refreshed." if not failed else f"\nfinished with failures: {failed}")
    print("open notebooks 11-14 in Jupyter to view the charts (they now hold the plots).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

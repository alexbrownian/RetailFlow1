# verify_gic.py
# =============
# Checks that step 1 (build aggregates + export) produced good, SAFE data.
#
#   python verify_gic.py
#
# It proves three things and prints a PASS/FAIL line for each:
#   1. PRESENT  - all five aggregate files exist in GIC_RAW_DATA and are small.
#   2. SAFE     - none of them carry a raw-revealing column (title, selftext,
#                 author, id, subreddit, score, num_comments). This is the whole
#                 point: only counts + sentiment scores may leave the machine.
#   3. MATCH    - each GIC_RAW_DATA file has the same row count as its
#                 data/processed source, so the export copied cleanly.
# It also prints rows + date range + size for each file so you can eyeball them.

import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
GIC_DIR = os.path.join(ROOT, "GIC_RAW_DATA")
PROCESSED_DIR = os.path.join(ROOT, "data", "processed")

FILES = [
    "daily_ticker_counts.parquet",
    "daily_ticker_counts_by_source.parquet",
    "daily_ticker_sentiment.parquet",
    "daily_theme_counts.parquet",
    "daily_theme_sentiment.parquet",
]

# Any of these columns in a committed file would leak the raw posts.
FORBIDDEN = {"title", "selftext", "author", "id", "subreddit",
             "score", "num_comments", "body", "text", "permalink"}

MAX_MB = 25          # a committed aggregate bigger than this is a red flag


def check_one(name):
    """Return (present, safe, rows, note) for one GIC file."""
    path = os.path.join(GIC_DIR, name)
    if not os.path.exists(path):
        return False, False, 0, "MISSING"

    size_mb = os.path.getsize(path) / (1024 * 1024)
    df = pd.read_parquet(path)
    cols = list(df.columns)
    bad = [c for c in cols if c.lower() in FORBIDDEN]
    safe = (len(bad) == 0) and (size_mb <= MAX_MB)

    # a readable one-line description
    date_range = ""
    if "date" in df.columns and len(df):
        d = pd.to_datetime(df["date"])
        date_range = f" | {d.min().date()} -> {d.max().date()}"
    note = f"{size_mb:5.2f} MB | {len(df):>7,} rows | cols={cols}{date_range}"
    if bad:
        note += f"  <-- LEAK: forbidden column(s) {bad}"
    if size_mb > MAX_MB:
        note += f"  <-- TOO BIG (> {MAX_MB} MB)"
    return True, safe, len(df), note


def match_processed(name, gic_rows):
    """Compare GIC row count to the data/processed source (if present)."""
    path = os.path.join(PROCESSED_DIR, name)
    if not os.path.exists(path):
        return None, "(no data/processed copy to compare)"
    src_rows = len(pd.read_parquet(path, columns=None))
    ok = (src_rows == gic_rows)
    return ok, f"processed={src_rows:,}  gic={gic_rows:,}  {'match' if ok else 'MISMATCH'}"


def main():
    print("=" * 74)
    print("VERIFY GIC_RAW_DATA  (step 1: build aggregates + export)")
    print("=" * 74)

    all_present = all_safe = all_match = True

    for name in FILES:
        present, safe, rows, note = check_one(name)
        print(f"\n{name}")
        print(f"  {note}")
        if not present:
            all_present = False
            all_safe = False
            all_match = False
            continue
        if not safe:
            all_safe = False
        ok, match_note = match_processed(name, rows)
        print(f"  export: {match_note}")
        if ok is False:
            all_match = False

    print("\n" + "-" * 74)
    print(f"  1. PRESENT (all five files exist)        : {'PASS' if all_present else 'FAIL'}")
    print(f"  2. SAFE (no raw-revealing columns/sizes) : {'PASS' if all_safe else 'FAIL'}")
    print(f"  3. MATCH (export copied every row)       : {'PASS' if all_match else 'FAIL'}")
    print("-" * 74)

    ok = all_present and all_safe and all_match
    print("RESULT:", "ALL GOOD - safe to commit." if ok
          else "PROBLEM - do NOT commit until fixed (see the flagged lines above).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

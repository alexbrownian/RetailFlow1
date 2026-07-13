# build_theme_counts.py
# =====================
# Rebuild data/processed/daily_theme_counts.parquet over the whole slice
# (posts_slice.parquet, written by notebook 01). Part of the --full chain:
# notebooks 02/06/07 rebuild ticker counts and sentiment, and THIS script
# rebuilds the theme counts - the one aggregate none of them produce.
#
#     python data_ingestion/scripts/build_theme_counts.py
#
# Counting rule (same as everywhere): one post counts once per theme it
# mentions, however many keywords match - breadth of attention.
# Memory-safe: the slice is streamed in batches, never loaded whole.

import os
import sys

import pandas as pd
import pyarrow.parquet as pq

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
sys.path.insert(0, PROJECT_ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src.themes import themes_in_text                       # noqa: E402
from src import abstracted_data                             # noqa: E402

SLICE_PATH = os.path.join(PROJECT_ROOT, "data", "processed", "posts_slice.parquet")
OUT_PATH = os.path.join(PROJECT_ROOT, "data", "processed", "daily_theme_counts.parquet")


def main():
    if not os.path.exists(SLICE_PATH):
        print("no posts_slice.parquet - run notebook 01 (or update_data.py --full) first.")
        return 1

    pf = pq.ParquetFile(SLICE_PATH)
    total = pf.metadata.num_rows
    print(f"scanning {total:,} posts for theme keywords (streamed)...")

    counts = {}                                  # (date, theme) -> post count
    seen = 0
    for batch in pf.iter_batches(columns=["date", "title", "selftext"],
                                 batch_size=100_000):
        dates = batch.column("date").to_pylist()
        titles = batch.column("title").to_pylist()
        bodies = batch.column("selftext").to_pylist()
        for date, title, body in zip(dates, titles, bodies):
            text = (title or "") + " " + (body or "")
            for theme in themes_in_text(text):
                key = (str(date)[:10], theme)
                counts[key] = counts.get(key, 0) + 1
        seen += len(dates)
        if seen % 1_000_000 < 100_000:
            print(f"  ... {seen:,}/{total:,} posts scanned", flush=True)

    rows = [{"date": d, "theme": t, "mention_count": n}
            for (d, t), n in counts.items()]
    daily = pd.DataFrame(rows).sort_values(["date", "theme"]).reset_index(drop=True)
    daily["date"] = pd.to_datetime(daily["date"])
    abstracted_data._safe_write(daily, OUT_PATH)
    print(f"wrote {len(daily):,} rows ({daily['date'].min().date()} -> "
          f"{daily['date'].max().date()}, {daily['theme'].nunique()} themes) "
          f"-> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

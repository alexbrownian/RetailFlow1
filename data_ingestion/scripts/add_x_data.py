# add_x_data.py
# =============
# STEP 2 of 2 for adding X (Twitter) data. Takes EVERY raw X dump found in
# data/raw/X Data/ (made by fetch_x_data.py), normalises each with its
# registered normaliser (src/x_data.py DATASETS), and rebuilds the X block
# of the main dataset:
#
#     posts.parquet columns:  ... the 8 classic ones ... + source
#     source = 'reddit' | 'x'    (X rows also have subreddit='x_twitter')
#
# IDEMPOTENT BY DESIGN: every run copies the Reddit rows and rebuilds the
# X rows from scratch from ALL raw files present. So the workflow for
# adding another dataset later is simply:
#     1. add a normaliser + registry line in src/x_data.py
#     2. python data_ingestion/scripts/fetch_x_data.py   (downloads new one)
#     3. python data_ingestion/scripts/add_x_data.py     (rebuilds X block)
# No new pipeline files, no duplicated tweets.
#
# HOW it works (streaming - never loads the 1.15 GB file into memory):
#   1. normalises every raw X file; concatenates; dedups on id across
#      datasets ("first seen wins" - real tweet ids are shared between
#      some datasets, so genuine duplicates collapse)
#   2. streams posts.parquet ROW GROUP BY ROW GROUP, keeping only the
#      reddit rows (adding source='reddit' if the column doesn't exist yet)
#   3. appends the tweets as their own date-sorted block, verifies row
#      counts and schema, then swaps the new file in (old kept as .bak
#      the first time; later runs just replace).
#
# Run from anywhere:  python data_ingestion/scripts/add_x_data.py
# Optional args:      --posts / --raw-dir / --out  (defaults: project paths)

import argparse
import glob
import io
import os
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import zstandard

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
sys.path.insert(0, PROJECT_ROOT)

from src.x_data import DATASETS, OUTPUT_COLUMNS  # noqa: E402

DEFAULT_POSTS = os.path.join(PROJECT_ROOT, "data", "processed", "posts.parquet")
DEFAULT_RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "X Data")

BATCH_SIZE = 250_000

SCHEMA = pa.schema([
    ("id", pa.string()),
    ("date", pa.string()),
    ("author", pa.string()),
    ("score", pa.int64()),
    ("subreddit", pa.string()),
    ("title", pa.string()),
    ("selftext", pa.string()),
    ("num_comments", pa.int64()),
    ("source", pa.string()),
])


def read_raw(path):
    """Decompress a .csv.zst and read the csv inside it."""
    with open(path, "rb") as f:
        data = zstandard.ZstdDecompressor().decompress(f.read())
    return pd.read_csv(io.BytesIO(data))


def load_all_tweets(raw_dir):
    """Normalise every raw X file found; dedup on id ACROSS datasets."""
    files = sorted(glob.glob(os.path.join(raw_dir, "*.csv.zst")))
    if not files:
        sys.exit(f"No raw X data in {raw_dir}\nRun fetch_x_data.py first.")

    parts = []
    for path in files:
        key = os.path.basename(path).replace(".csv.zst", "")
        if key not in DATASETS:
            print(f"[warn] {key}: not in the registry (src/x_data.py) - skipped")
            continue
        print(f"[norm] {key} ...")
        raw = read_raw(path)
        tweets = DATASETS[key]["normaliser"](raw)
        print(f"       kept {len(tweets):,} of {len(raw):,} raw rows "
              f"| dates {tweets['date'].min()} -> {tweets['date'].max()}")
        parts.append(tweets)

    if not parts:
        sys.exit("No registered raw files found - nothing to merge.")
    all_tweets = pd.concat(parts, ignore_index=True)
    before = len(all_tweets)
    all_tweets = (all_tweets.drop_duplicates(subset="id", keep="first")
                  .sort_values("date").reset_index(drop=True))
    if before - len(all_tweets):
        print(f"[dedup] {before - len(all_tweets):,} duplicate ids across datasets dropped")
    return all_tweets


def main():
    p = argparse.ArgumentParser(description="(Re)build the X block of posts.parquet")
    p.add_argument("--posts", default=DEFAULT_POSTS)
    p.add_argument("--raw-dir", default=DEFAULT_RAW_DIR)
    p.add_argument("--out", default=None, help="default: <posts dir>/posts_with_x.parquet")
    args = p.parse_args()

    # ---- 1. normalise all raw X files first (fail early if one is bad)
    tweets = load_all_tweets(args.raw_dir)
    print(f"[all ] {len(tweets):,} unique tweets total "
          f"| dates {tweets['date'].min()} -> {tweets['date'].max()}")

    # ---- 2. stream-copy the reddit rows (drop any previously merged x rows)
    pf = pq.ParquetFile(args.posts)
    has_source = "source" in pf.schema_arrow.names
    out_path = args.out or os.path.join(os.path.dirname(args.posts), "posts_with_x.parquet")

    writer = pq.ParquetWriter(out_path, SCHEMA, compression="zstd")
    reddit_rows = 0
    for i in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(i)
        if has_source:
            t = t.filter(pc.equal(t["source"], "reddit"))
        else:
            t = t.append_column("source", pa.array(["reddit"] * t.num_rows))
        if t.num_rows:
            writer.write_table(t)
            reddit_rows += t.num_rows
        print(f"  row group {i + 1}/{pf.metadata.num_row_groups} "
              f"({reddit_rows:,} reddit rows kept)", flush=True)

    # ---- 3. append the tweets as their own date-sorted block
    for start in range(0, len(tweets), BATCH_SIZE):
        chunk = tweets.iloc[start:start + BATCH_SIZE]
        writer.write_table(pa.Table.from_pandas(chunk, schema=SCHEMA, preserve_index=False))
    writer.close()

    # Windows refuses to rename a file that ANY process (including this one)
    # still has open - so release our own read handle before the swap.
    try:
        pf.close()
    except AttributeError:      # very old pyarrow has no .close()
        del pf

    # ---- 4. verify, then swap files
    new = pq.ParquetFile(out_path)
    assert new.schema_arrow.names == [f.name for f in SCHEMA], "schema mismatch"
    assert new.metadata.num_rows == reddit_rows + len(tweets), (
        f"row count mismatch: {new.metadata.num_rows} != {reddit_rows} + {len(tweets)}")
    print(f"verified: {reddit_rows:,} reddit + {len(tweets):,} x = "
          f"{new.metadata.num_rows:,} rows")

    backup = args.posts.replace(".parquet", ".reddit_only.bak.parquet")
    try:
        if not os.path.exists(backup) and not has_source:
            os.replace(args.posts, backup)
            print("first merge: old file kept at", backup)
        else:
            os.remove(args.posts)
        os.replace(out_path, args.posts)
    except PermissionError:
        # Another process (usually a Jupyter kernel - notebook 01 keeps the
        # parquet open) is holding posts.parquet. The merged file is safe;
        # only the rename is missing. Tell the user how to finish by hand.
        print()
        print("!" * 68)
        print("The merge SUCCEEDED but the final rename failed: another program")
        print("has posts.parquet open (usually a running Jupyter kernel).")
        print("Close all kernels / Excel / viewers, then EITHER re-run this")
        print("script (fast - the merged file is rebuilt) OR rename by hand:")
        print(f'  ren "{args.posts}" "{os.path.basename(backup)}"')
        print(f'  ren "{out_path}" "{os.path.basename(args.posts)}"')
        print("!" * 68)
        return 1
    print("swapped in:", args.posts)
    print("next: re-run notebook 01 (slice + screening), then 02.")
    print("also: pin EXPECTED_X_ROWS in tests/test_pipeline.py to", len(tweets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# fetch_x_data.py
# ===============
# STEP 1 of 2 for adding X (Twitter) data. Downloads EVERY dataset in the
# registry (src/x_data.py DATASETS) from HuggingFace and stores each RAW -
# exactly as downloaded, zstd-compressed - as:
#
#     data/raw/X Data/<key>.csv.zst
#
# Currently registered:
#   financial_tweets           ~315k rows, Nov 2023+   (~hundreds of MB)
#   stock_market_tweets_data   ~924k rows, Apr-Jul 2020 (~175 MB)
#   stock_market_tweets        millions of rows, 2015-2020 (LARGE - be patient)
#
# Files that already exist are SKIPPED, so re-running after adding a new
# dataset to the registry only downloads the new one. The raw files are the
# immutable source of truth (same philosophy as the Reddit .zst dumps).
#
# Requirements (one-time):  pip install huggingface_hub fsspec zstandard
# Run from anywhere:        python data_ingestion/scripts/fetch_x_data.py
#                           (--only <key> to fetch a single dataset)
# Then run step 2:          python data_ingestion/scripts/add_x_data.py

import argparse
import io
import os
import sys

import pandas as pd
import zstandard

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
sys.path.insert(0, PROJECT_ROOT)

from src.x_data import DATASETS  # noqa: E402

OUT_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "X Data")


def fetch_one(key, repo):
    out_file = os.path.join(OUT_DIR, key + ".csv.zst")
    if os.path.exists(out_file):
        print(f"[skip] {key} - already downloaded ({out_file})")
        return

    # Find the csv file(s) in the repo without hardcoding their names.
    from huggingface_hub import HfFileSystem
    fs = HfFileSystem()
    csvs = fs.glob(f"datasets/{repo}/*.csv") + fs.glob(f"datasets/{repo}/**/*.csv")
    csvs = sorted(set(csvs))
    if not csvs:
        print(f"[warn] {key}: no csv files found in {repo} - skipping")
        return

    print(f"[get ] {key}: {len(csvs)} csv file(s) from {repo} ...")
    frames = [pd.read_csv("hf://" + p) for p in csvs]
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    print(f"       downloaded {len(df):,} rows, columns: {df.columns.tolist()}")

    buffer = io.BytesIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)
    with open(out_file, "wb") as f:
        f.write(zstandard.ZstdCompressor(level=10).compress(buffer.read()))
    print(f"       saved raw -> {out_file} ({os.path.getsize(out_file) / 1e6:.0f} MB)")


def main():
    p = argparse.ArgumentParser(description="Download the registered X datasets")
    p.add_argument("--only", choices=sorted(DATASETS), default=None,
                   help="fetch just this dataset")
    args = p.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    for key, spec in DATASETS.items():
        if args.only and key != args.only:
            continue
        fetch_one(key, spec["repo"])

    print("\nnext: python data_ingestion/scripts/add_x_data.py")


if __name__ == "__main__":
    main()

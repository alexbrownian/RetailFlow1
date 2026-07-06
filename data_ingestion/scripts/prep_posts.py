# prep_posts.py
# =============
# THE one ingestion script: turns the raw dumps in data/raw/ into the file the
# rest of the project runs on: data/processed/posts.parquet
#
# (It replaces the old prep_for_pipeline.py / prep_all_dates.py pair - this
#  version does both jobs: full-dump rebuilds AND filtered slices.)
#
# What it does, in order:
#   1. finds every raw file in data/raw/ (.zst / .ndjson / .csv / .parquet)
#   2. streams them record by record (never loads a whole file into memory)
#   3. normalises each record to the 8 standard columns using the project's
#      own src/clean_data.py, so the output schema never drifts:
#          id, date, author, score, subreddit, title, selftext, num_comments
#   4. applies the optional subreddit / date filters below
#   5. DEDUPLICATES by post id - "first seen wins". The archive crawler
#      sometimes captured the same post twice (pinned posts especially);
#      the first occurrence has the date closest to the true posting date.
#      This is the same rule live ingestion will use: an id that is already
#      in the dataset never gets ingested again.
#   6. writes data/processed/posts.parquet in 250k-row batches, so memory
#      stays low even on the full 30M+ record dump
#
# How to run (from anywhere; paths are worked out automatically):
#   python3 data_ingestion/scripts/prep_posts.py
#
# Note: the dedup set holds every post id in memory (~8M ids = ~0.7 GB for
# the full dump). Fine on a normal laptop; set DEDUPE = False if you ever
# need to trade correctness for memory.

import os
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ----------------------------------------------------------------------
# PARAMETERS - edit these
# ----------------------------------------------------------------------
SUBREDDITS  = []      # e.g. ['wallstreetbets', 'stocks'];  [] = keep ALL
USE_FINANCE_LIST = False  # True = ignore SUBREDDITS above and use
                          # data_ingestion/finance_subreddits.txt instead
START_DATE  = None    # e.g. '2021-01-01' (inclusive), or None
END_DATE    = None    # e.g. '2021-07-01' (EXCLUSIVE),  or None
DEDUPE      = True    # drop repeated post ids (first seen wins)
BATCH_SIZE  = 250_000 # rows held in memory before flushing to disk

# ----------------------------------------------------------------------
# Work out folders relative to this script (scripts -> data_ingestion -> root)
# ----------------------------------------------------------------------
THIS_DIR     = os.path.dirname(os.path.abspath(__file__))
INGEST_DIR   = os.path.dirname(THIS_DIR)
PROJECT_ROOT = os.path.dirname(INGEST_DIR)

RAW_FOLDER  = os.path.join(PROJECT_ROOT, "data", "raw")
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "data", "processed", "posts.parquet")
SUBS_FILE   = os.path.join(INGEST_DIR, "finance_subreddits.txt")

# Let Python import the project's own cleaning code.
sys.path.insert(0, PROJECT_ROOT)
from src.clean_data import find_input_files, read_json_lines, normalise, keep_this_post, OUTPUT_COLUMNS

# A fixed schema so every batch is written with identical column types.
# 'source' tags where each row came from ('reddit' here). X (Twitter) data
# is NOT handled by this script: it lives in data/raw/X Data/ (a subfolder,
# which find_input_files deliberately does not recurse into) and is merged
# afterwards by add_x_data.py. After any rebuild here, re-run add_x_data.py.
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


def read_subreddit_list(path):
    """Read finance_subreddits.txt, skipping blank lines and # comments."""
    names = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                names.append(line)
    return names


def write_batch(writer, rows):
    """Turn a list of post dicts into a table and append it to the parquet file."""
    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    table = pa.Table.from_pandas(df, schema=SCHEMA, preserve_index=False)
    writer.write_table(table)


def main():
    if USE_FINANCE_LIST:
        wanted = set(s.lower() for s in read_subreddit_list(SUBS_FILE))
    else:
        wanted = set(s.lower() for s in SUBREDDITS)

    files = find_input_files(RAW_FOLDER)
    print("Found", len(files), "raw file(s) in", RAW_FOLDER)
    print("Subreddits :", sorted(wanted) if wanted else "ALL")
    print("Date range :", START_DATE, "to", END_DATE, "| dedupe:", DEDUPE)
    print("-" * 60)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    writer = pq.ParquetWriter(OUTPUT_FILE, SCHEMA, compression="zstd")

    seen_ids = set()      # every id we've already written (dedup, first wins)
    total_kept = 0
    total_dupes = 0
    batch = []

    for filepath in files:
        name = os.path.basename(filepath)
        kept_from_file = 0
        print("reading", name, "...", flush=True)

        for record in read_json_lines(filepath):
            post = normalise(record)
            if not keep_this_post(post, wanted, START_DATE, END_DATE):
                continue
            if DEDUPE:
                if post["id"] in seen_ids:
                    total_dupes += 1
                    continue          # already have this post - first seen wins
                seen_ids.add(post["id"])
            batch.append(post)
            kept_from_file += 1
            if len(batch) >= BATCH_SIZE:
                write_batch(writer, batch)
                total_kept += len(batch)
                batch = []
                print("  ...", total_kept, "posts written so far", flush=True)

        # Flush what's left of this file so each subreddit stays in one block.
        if batch:
            write_batch(writer, batch)
            total_kept += len(batch)
            batch = []
        print("  done:", kept_from_file, "posts kept from", name, flush=True)

    writer.close()
    print("-" * 60)
    print("Finished. %d posts written -> %s" % (total_kept, OUTPUT_FILE))
    print("Duplicate posts skipped: %d" % total_dupes)
    print("Next: open notebooks/01_clean_data.ipynb (or 02) - they read this file directly.")


if __name__ == "__main__":
    main()

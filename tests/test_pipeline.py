"""
test_pipeline.py
================
Pytest checks for the data ingestion pipeline and the analysis steps the
notebooks use. Run from the project root with:

    pytest tests/ -v

Four groups of tests:
  1. The dataset  - data/processed/posts.parquet is present, has the right
                    schema, the right row counts, and sane dates.
  2. The units    - extract_tickers / build_mentions / inflection behave
                    correctly on tiny hand-made examples where we KNOW the
                    right answer.
  3. The notebooks - all valid JSON, none reading raw .zst files.
  4. End-to-end   - a real slice of posts.parquet flows through
                    build_daily_counts without errors, producing the columns
                    the notebooks expect.
"""

import datetime
import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

# Make "src" importable no matter where pytest is launched from.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.extract_tickers import extract_tickers_from_text
from src.build_mentions import build_daily_counts
from src.inflection import compute_inflection

POSTS_PATH = PROJECT_ROOT / "data" / "processed" / "posts.parquet"

EXPECTED_COLUMNS = ["id", "date", "author", "score", "subreddit",
                    "title", "selftext", "num_comments"]

# What the 2026-07-02 ingestion produced (see data/README.md).
EXPECTED_TOTAL_ROWS = 7_954_297
EXPECTED_SUBREDDITS = {
    "bitcoin": 1_164_734,
    "cryptocurrency": 1_715_219,
    "daytrading": 89_512,
    "dividends": 37_074,
    "financialindependence": 88_134,
    "investing": 336_252,
    "options": 109_680,
    "pennystocks": 199_236,
    "personalfinance": 1_383_818,
    "securityanalysis": 27_155,
    "stockmarket": 150_279,
    "stocks": 322_977,
    "thetagang": 33_314,
    "valueinvesting": 18_794,
    "wallstreetbets": 2_278_119,
}

# 4 pinned WSB posts were crawled more than once by the archive (5 extra rows).
# Documented, immaterial for analysis; prep_posts.py dedupes on any rebuild.
KNOWN_DUPLICATE_ROWS = 5


# ----------------------------------------------------------------------
# Group 1 - the dataset itself
# ----------------------------------------------------------------------
def test_posts_parquet_exists():
    assert POSTS_PATH.exists(), "posts.parquet is missing - run data_ingestion/scripts/prep_posts.py"


def test_schema_columns():
    # Reading only metadata - fast even on a 1 GB file.
    schema = pq.ParquetFile(POSTS_PATH).schema_arrow
    assert schema.names == EXPECTED_COLUMNS


def test_total_row_count():
    n = pq.ParquetFile(POSTS_PATH).metadata.num_rows
    assert n == EXPECTED_TOTAL_ROWS


def test_per_subreddit_counts():
    # Load just the one small column we need, not the whole table.
    subs = pq.read_table(POSTS_PATH, columns=["subreddit"]).to_pandas()["subreddit"]
    counts = subs.value_counts().to_dict()
    assert counts == EXPECTED_SUBREDDITS


def test_dates_are_valid():
    dates = pq.read_table(POSTS_PATH, columns=["date"]).to_pandas()["date"]
    # No empty dates (records without a parseable created_utc were dropped).
    assert (dates != "").all()
    # All within a plausible range.
    assert dates.min() >= "2008-01-01"
    assert dates.max() <= "2026-01-01"
    # Spot-check the format is YYYY-MM-DD by parsing a sample strictly.
    sample = dates.sample(1000, random_state=0)
    for d in sample:
        datetime.datetime.strptime(d, "%Y-%m-%d")


def test_subreddit_is_lowercase():
    subs = pq.read_table(POSTS_PATH, columns=["subreddit"]).to_pandas()["subreddit"]
    assert (subs == subs.str.lower()).all()


def test_no_unexpected_duplicate_ids():
    # The current file carries exactly 5 known duplicate rows (pinned WSB
    # posts the crawler captured twice). Any rebuild via prep_posts.py has
    # DEDUPE on, so this number must only ever go DOWN.
    ids = pq.read_table(POSTS_PATH, columns=["id"]).to_pandas()["id"]
    duplicate_rows = len(ids) - ids.nunique()
    assert duplicate_rows <= KNOWN_DUPLICATE_ROWS, (
        f"{duplicate_rows} duplicate rows found (expected <= {KNOWN_DUPLICATE_ROWS}). "
        "New duplicates got into the dataset - rebuild with prep_posts.py (DEDUPE=True)."
    )


# ----------------------------------------------------------------------
# Group 2 - unit tests with hand-made examples
# ----------------------------------------------------------------------
UNIVERSE = {"GME", "AMC", "NVDA", "TSLA", "CEO"}  # CEO is in the stop list


def test_cashtag_is_found():
    assert extract_tickers_from_text("$GME to the moon", UNIVERSE, cashtags_only=True) == ["GME"]


def test_stopword_is_ignored_even_if_in_universe():
    # "CEO" is finance jargon, not a mention, so the stop list must win.
    assert extract_tickers_from_text("our CEO said $CEO", UNIVERSE, cashtags_only=False) == []


def test_cashtags_only_skips_bare_words():
    text = "GME is mooning"
    assert extract_tickers_from_text(text, UNIVERSE, cashtags_only=True) == []


def test_build_daily_counts_dedupes_and_weights():
    # One post mentions $GME twice -> must count as ONE mention,
    # weighted_count must be score squared (10^2 = 100).
    posts = pd.DataFrame([
        {"date": "2021-01-27", "title": "$GME $GME!!", "selftext": "", "score": 10},
        {"date": "2021-01-27", "title": "boring day", "selftext": "nothing here", "score": 50},
    ])
    counts = build_daily_counts(posts, UNIVERSE, cashtags_only=True)
    gme = counts[(counts.ticker == "GME") & (counts.date == "2021-01-27")]
    assert len(gme) == 1
    assert int(gme.mention_count.iloc[0]) == 1
    assert int(gme.weighted_count.iloc[0]) == 100


def test_compute_inflection_flags_a_spike():
    # 20 quiet days of ~2 mentions, then a huge jump - the jump day(s)
    # must be flagged, the quiet days must not.
    quiet = [2] * 20
    spike = [2, 40, 80]
    series = pd.Series(
        quiet + spike,
        index=pd.date_range("2021-01-01", periods=23, freq="D"),
    )
    result = compute_inflection(series, smooth_window=3, k=2.0)
    assert result.columns.tolist() == ["count", "smoothed", "velocity", "is_inflection"]
    assert not result["is_inflection"].iloc[:20].any(), "quiet days wrongly flagged"
    assert result["is_inflection"].iloc[21:].any(), "the spike was not detected"


# ----------------------------------------------------------------------
# Group 3 - the notebooks themselves
# ----------------------------------------------------------------------
def test_notebooks_are_valid_json():
    # Notebooks ar
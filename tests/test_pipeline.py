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

from src.extract_tickers import extract_tickers_from_text, load_cashtag_only_tickers
from src.build_mentions import build_daily_counts
from src.inflection import compute_inflection
from src.screen_tickers import classify_ticker, count_caps_vs_lower
from src.themes import (
    THEME_KEYWORDS,
    build_daily_theme_counts,
    build_inferred_theme_counts,
    combine_theme_signals,
    themes_in_text,
)

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


def test_screening_case_ratio_wins_over_wordfreq():
    # Seen often and caps-heavy in OUR corpus -> ticker, even if wordfreq
    # thinks it's a word (this is the SNAP / AMD case).
    assert classify_ticker(caps_count=60, lower_count=20, zipf=4.2) == ("normal", "case_ratio")
    # Seen often and mostly lowercase -> word (the EDGE / LOAN case).
    assert classify_ticker(caps_count=5, lower_count=500, zipf=4.7) == ("cashtag_only", "case_ratio")


def test_screening_wordfreq_fallback_for_rare_tokens():
    # Too rare in the corpus to trust the ratio -> wordfreq decides.
    assert classify_ticker(caps_count=2, lower_count=3, zipf=4.7) == ("cashtag_only", "wordfreq")
    assert classify_ticker(caps_count=2, lower_count=3, zipf=1.5) == ("normal", "wordfreq")


def test_count_caps_vs_lower_counts_both_casings():
    texts = ["the edge of tomorrow", "EDGE is a ticker", "Edge case", "bought NVDA"]
    caps, lower = count_caps_vs_lower(texts, {"EDGE", "NVDA"})
    assert caps["EDGE"] == 1      # "EDGE"
    assert lower["EDGE"] == 2     # "edge" + "Edge" (sentence-start counts as word)
    assert caps["NVDA"] == 1
    assert lower["NVDA"] == 0


def test_load_cashtag_only_tickers_missing_file_is_empty():
    # Without the CSV the extractor must behave exactly as before.
    assert load_cashtag_only_tickers(Path("does/not/exist.csv")) == frozenset()


def test_screened_ticker_bare_word_ignored_but_cashtag_counts(tmp_path):
    # Simulate a classification CSV that demotes EDGE, then check the rule
    # the extractor applies: bare 'EDGE' ignored, '$EDGE' still a mention.
    csv = tmp_path / "cls.csv"
    csv.write_text("ticker,classification\nEDGE,cashtag_only\nNVDA,normal\n")
    demoted = load_cashtag_only_tickers(csv)
    assert demoted == {"EDGE"}

    universe = {"EDGE", "NVDA"}
    text = "EDGE has no EDGE but $EDGE and NVDA do"
    hits = extract_tickers_from_text(text, universe, cashtags_only=False)
    # The real module-level SCREENED_STOP is loaded from the repo CSV; if
    # that file exists EDGE is already demoted. Either way $EDGE + NVDA count.
    assert "NVDA" in hits
    assert "EDGE" in hits  # the cashtag one
    from src.extract_tickers import SCREENED_STOP
    if "EDGE" in SCREENED_STOP:
        assert hits.count("EDGE") == 1  # only the $EDGE mention


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
    # inflection.py grew extra columns (is_rise/is_fall/is_peak/is_trough);
    # the notebooks only rely on these four core ones existing.
    for col in ["count", "smoothed", "velocity", "is_inflection"]:
        assert col in result.columns
    assert not result["is_inflection"].iloc[:20].any(), "quiet days wrongly flagged"
    assert result["is_inflection"].iloc[21:].any(), "the spike was not detected"


def test_theme_keywords_contain_no_bare_ticker_symbols():
    # Keyword matching is case-insensitive, so a bare symbol like C or O
    # would match every ordinary "c"/"o" in prose. Ticker exposure belongs
    # in THEME_TICKERS (the inferred signal), never in THEME_KEYWORDS.
    known_offenders = {"C", "O", "MU", "LI", "BP", "MS", "GS", "JD",
                       "GLD", "SLV", "NVDA", "TSLA", "GME", "AMC"}
    for theme, keywords in THEME_KEYWORDS.items():
        hits = known_offenders.intersection(keywords)
        assert not hits, f"theme '{theme}' contains bare ticker symbol(s): {hits}"


def test_themes_in_text_words_and_phrases():
    found = themes_in_text("HBM memory pricing is up, Micron capacity tight")
    assert "memory" in found
    found = themes_in_text("this is a short squeeze on a low float stock")
    assert "short_squeeze" in found
    # Whole-token matching: "golden" must NOT hit the gold theme.
    assert "gold_metals" not in themes_in_text("the golden retriever era")
    assert "gold_metals" in themes_in_text("buying gold as an inflation hedge")


def test_build_daily_theme_counts_dedupes_and_weights():
    # One post hits the same theme via several keywords -> counts ONCE,
    # weighted by score squared (10^2 = 100). Same rules as the ticker side.
    posts = pd.DataFrame([
        {"date": "2021-01-27", "title": "gold and silver and copper!",
         "selftext": "more gold talk", "score": 10},
        {"date": "2021-01-27", "title": "nothing thematic here at all",
         "selftext": "", "score": 50},
    ])
    counts = build_daily_theme_counts(posts)
    gold = counts[(counts.theme == "gold_metals") & (counts.date == "2021-01-27")]
    assert len(gold) == 1
    assert int(gold.keyword_count.iloc[0]) == 1
    assert int(gold.keyword_weighted.iloc[0]) == 100


def test_build_inferred_theme_counts_maps_tickers_to_multiple_themes():
    ticker_counts = pd.DataFrame([
        {"date": "2021-01-27", "ticker": "NVDA", "mention_count": 7, "weighted_count": 49},
        {"date": "2021-01-27", "ticker": "ZZZZ", "mention_count": 3, "weighted_count": 9},
    ])
    inferred = build_inferred_theme_counts(ticker_counts)
    themes = set(inferred.theme)
    # NVDA feeds all three of its themes; the unmapped ticker feeds none.
    assert {"semiconductors", "ai", "ai_megacap"} <= themes
    row = inferred[inferred.theme == "ai"].iloc[0]
    assert int(row.inferred_count) == 7
    assert int(row.inferred_weighted) == 49


def test_combine_theme_signals_outer_joins_with_zeros():
    kw = pd.DataFrame([{"date": "2021-01-27", "theme": "ai",
                        "keyword_count": 5, "keyword_weighted": 25}])
    inf = pd.DataFrame([{"date": "2021-01-27", "theme": "energy",
                         "inferred_count": 3, "inferred_weighted": 9}])
    combined = combine_theme_signals(kw, inf)
    assert len(combined) == 2
    ai = combined[combined.theme == "ai"].iloc[0]
    assert int(ai.keyword_count) == 5 and int(ai.inferred_count) == 0
    energy = combined[combined.theme == "energy"].iloc[0]
    assert int(energy.inferred_count) == 3 and int(energy.keyword_count) == 0


# ----------------------------------------------------------------------
# Group 3 - the notebooks themselves
# ----------------------------------------------------------------------
def test_notebooks_are_valid_json():
    # Notebooks are JSON; hand-editing them breaks the whole file (unescaped
    # quotes etc.). This catches it early. Fix with:
    #   python3 data_ingestion/scripts/check_notebooks.py --fix
    nb_dir = PROJECT_ROOT / "notebooks"
    import json
    for path in sorted(nb_dir.glob("*.ipynb")):
        with open(path, encoding="utf-8") as f:
            json.load(f)  # raises with line/column if broken


def test_notebooks_do_not_read_zst():
    # Analysis notebooks must read posts.parquet, never the raw .zst dumps.
    # (Raw-to-parquet conversion lives in data_ingestion/scripts/.)
    import json
    nb_dir = PROJECT_ROOT / "notebooks"
    for path in sorted(nb_dir.glob("*.ipynb")):
        nb = json.load(open(path, encoding="utf-8"))
        for cell in nb["cells"]:
            if cell["cell_type"] == "code":
                src = "".join(cell["source"])
                assert ".zst" not in src, f"{path.name} still references .zst files"


# ----------------------------------------------------------------------
# Group 4 - end-to-end on a real slice of the dataset
# ----------------------------------------------------------------------
def test_real_slice_through_pipeline():
    # Take one small subreddit block (daytrading, ~90k posts) straight from
    # the real parquet and push it through the same steps notebook 02 uses.
    table = pq.read_table(
        POSTS_PATH,
        columns=["date", "subreddit", "title", "selftext", "score"],
        filters=[("subreddit", "=", "daytrading")],
    )
    posts = table.to_pandas()
    assert len(posts) == EXPECTED_SUBREDDITS["daytrading"]

    # A tiny fixed universe keeps the test fast and internet-free.
    counts = build_daily_counts(posts, {"GME", "AMC", "TSLA", "SPY"}, cashtags_only=True)
    assert list(counts.columns) == ["date", "ticker", "mention_count", "weighted_count"]
    assert (counts["mention_count"] > 0).all()

    # Daytraders definitely talked about SPY at least once in 16 years.
    assert "SPY" in set(counts["ticker"])

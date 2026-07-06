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
from src.x_data import normalise_tweets, normalise_smt, normalise_mjw, normalise_x_api

POSTS_PATH = PROJECT_ROOT / "data" / "processed" / "posts.parquet"

EXPECTED_COLUMNS = ["id", "date", "author", "score", "subreddit",
                    "title", "selftext", "num_comments"]

# True once add_x_data.py has merged the X (Twitter) rows (or prep_posts.py
# has rebuilt with the 9-column schema). The dataset tests below adapt.
HAS_SOURCE = "source" in pq.ParquetFile(POSTS_PATH).schema_arrow.names

# What the 2026-07-02 Reddit ingestion produced (see data/README.md).
EXPECTED_TOTAL_ROWS = 7_954_297   # reddit rows - pinned forever
# Pin this to the exact number add_x_data.py prints after each merge.
# It depends on how many of the registered datasets were fetched (three
# registered: ~315k + ~924k + several million rows, minus cross-dataset
# id dedup), so no fixed band is asserted until you pin it.
EXPECTED_X_ROWS = None
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
    if HAS_SOURCE:
        assert schema.names == EXPECTED_COLUMNS + ["source"]
    else:
        assert schema.names == EXPECTED_COLUMNS


def test_total_row_count():
    if HAS_SOURCE:
        # Reddit rows are pinned exactly; X rows are checked separately.
        src = pq.read_table(POSTS_PATH, columns=["source"]).to_pandas()["source"]
        counts = src.value_counts().to_dict()
        assert counts.get("reddit") == EXPECTED_TOTAL_ROWS
        x_rows = counts.get("x", 0)
        if EXPECTED_X_ROWS is not None:
            assert x_rows == EXPECTED_X_ROWS
        else:
            assert x_rows > 0, (
                "source column exists but no X rows found - "
                "did add_x_data.py finish? Pin EXPECTED_X_ROWS after it runs.")
    else:
        n = pq.ParquetFile(POSTS_PATH).metadata.num_rows
        assert n == EXPECTED_TOTAL_ROWS


def test_per_subreddit_counts():
    # Load just the one small column we need, not the whole table.
    subs = pq.read_table(POSTS_PATH, columns=["subreddit"]).to_pandas()["subreddit"]
    counts = subs.value_counts().to_dict()
    counts.pop("x_twitter", None)   # X rows live in their own pseudo-subreddit
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


def test_build_daily_counts_dedupes_and_has_no_weighted_column():
    # One post mentions $GME twice -> must count as ONE mention.
    # weighted_count (score**2) was REMOVED 2026-07-06: archived scores are
    # FINAL scores, so weighting day-t mentions by them leaks the future
    # into backtests. This test also guards against it sneaking back.
    posts = pd.DataFrame([
        {"date": "2021-01-27", "title": "$GME $GME!!", "selftext": "", "score": 10},
        {"date": "2021-01-27", "title": "boring day", "selftext": "nothing here", "score": 50},
    ])
    counts = build_daily_counts(posts, UNIVERSE, cashtags_only=True)
    assert list(counts.columns) == ["date", "ticker", "mention_count"]
    gme = counts[(counts.ticker == "GME") & (counts.date == "2021-01-27")]
    assert len(gme) == 1
    assert int(gme.mention_count.iloc[0]) == 1


def test_delisted_supplement_fixes_survivorship():
    # Today's Nasdaq symbol files omit delisted names (BBBY etc.), which
    # would silently drop the meme casualties from history - survivorship
    # bias that flatters backtests. The supplement re-adds them.
    from src.ticker_universe import DELISTED_TICKERS, _SYMBOL_OK
    assert "BBBY" in DELISTED_TICKERS and "SPRT" in DELISTED_TICKERS
    for sym in DELISTED_TICKERS:
        assert _SYMBOL_OK.fullmatch(sym), f"bad symbol in supplement: {sym}"
    # And the extractor counts them like any other universe member.
    hits = extract_tickers_from_text("$BBBY to the moon", {"BBBY"}, cashtags_only=True)
    assert hits == ["BBBY"]


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


def test_normalise_tweets_maps_dedupes_and_drops_bad_rows():
    raw = pd.DataFrame([
        {"timestamp": "2023-11-14T23:06:39.390000+00:00",
         "description": "$GME to the moon",
         "url": "https://twitter.com/user/status/111",
         "embed_title": "Crypto Mikey tweeted about GME", "tweet_type": "tweet"},
        {"timestamp": "2023-11-15T10:00:00.000000+00:00",
         "description": "same id again - must be dropped (first seen wins)",
         "url": "https://twitter.com/user/status/111",
         "embed_title": "Someone tweeted about GME", "tweet_type": "tweet"},
        {"timestamp": "not-a-date", "description": "bad date - dropped",
         "url": "https://twitter.com/user/status/222",
         "embed_title": "", "tweet_type": "tweet"},
    ])
    out = normalise_tweets(raw)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["id"] == "x_111"                 # prefixed - can't hit a reddit id
    assert row["date"] == "2023-11-14"
    assert row["source"] == "x"
    assert row["subreddit"] == "x_twitter"
    assert row["author"] == "Crypto Mikey"
    assert row["title"] == "$GME to the moon"   # tweet text lands in title
    assert row["score"] == 0                    # dataset has no like counts


def test_normalise_smt_row_ids_and_dates():
    # stock-market-tweets-data: id is a ROW NUMBER -> needs the x_smt_ prefix.
    raw = pd.DataFrame([
        {"id": 1, "created_at": "2020-04-09 23:59:51+00:00", "text": "$SPX to 10,000"},
        {"id": 2, "created_at": "2020-04-10 08:00:00+00:00", "text": ""},  # empty -> dropped
    ])
    out = normalise_smt(raw)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["id"] == "x_smt_1"           # dataset-scoped prefix
    assert row["date"] == "2020-04-09"
    assert row["score"] == 0 and row["author"] == ""
    assert row["source"] == "x" and row["subreddit"] == "x_twitter"


def test_normalise_x_api_live_shares_prefix_with_dumps():
    # Live official-API tweets get the SAME 'x_' prefix as the historical
    # dumps, so a tweet in both can never double-count (first seen wins).
    raw = pd.DataFrame([
        {"id": "1724564208602513837", "created_at": "2026-07-05T14:00:00.000Z",
         "text": "$GLD breaking out", "author": "goldbug", "likes": 42},
        {"id": "1724564208602513837", "created_at": "2026-07-05T15:00:00.000Z",
         "text": "dup id - dropped", "author": "", "likes": 0},
        {"id": "9", "created_at": "not-a-date", "text": "bad date - dropped",
         "author": "", "likes": 0},
    ])
    out = normalise_x_api(raw)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["id"] == "x_1724564208602513837"
    assert row["date"] == "2026-07-05"
    assert row["source"] == "x" and row["subreddit"] == "x_twitter"
    assert row["score"] == 42   # like count kept for spam filtering only


def test_normalise_mjw_engagement_and_per_ticker_dedup():
    # mjw/stock_market_tweets: real tweet ids, likes/comments, and the same
    # tweet repeated once per ticker_symbol - must collapse to ONE row.
    raw = pd.DataFrame([
        {"tweet_id": 550441509175443456, "writer": "VisualStockRSRC",
         "post_date": "2015-01-01 00:00:00", "body": "$AAPL and $MSFT lookin good",
         "comment_num": 2, "retweet_num": 1, "like_num": 5, "ticker_symbol": "AAPL"},
        {"tweet_id": 550441509175443456, "writer": "VisualStockRSRC",
         "post_date": "2015-01-01 00:00:00", "body": "$AAPL and $MSFT lookin good",
         "comment_num": 2, "retweet_num": 1, "like_num": 5, "ticker_symbol": "MSFT"},
    ])
    out = normalise_mjw(raw)
    assert len(out) == 1                    # per-ticker duplicate collapsed
    row = out.iloc[0]
    assert row["id"] == "x_550441509175443456"
    assert row["date"] == "2015-01-01"
    assert row["score"] == 5                # like_num -> score
    assert row["num_comments"] == 2         # comment_num -> num_comments
    assert row["author"] == "VisualStockRSRC"


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


def test_every_theme_has_a_tradeable_anchor():
    # Tradeable-by-design: each theme must map to an instrument in
    # THEME_ETFS, so a mention/sentiment spike always points at something
    # you can back-test and (eventually) trade.
    from src.themes import THEME_ETFS
    for theme in THEME_KEYWORDS:
        assert theme in THEME_ETFS and THEME_ETFS[theme], f"theme '{theme}' has no ETF anchor"


def test_sentiment_lexicon_and_aggregation():
    from src.sentiment import score_text, add_sentiment, build_daily_ticker_sentiment
    # WSB lexicon: slang must move the score the right way.
    assert score_text("GME to the moon, diamond hands, buying calls") > 0.3
    assert score_text("this is a rug, bagholders getting rekt") < -0.3
    assert abs(score_text("the quarterly report was published on Tuesday")) < 0.3

    posts = pd.DataFrame([
        {"date": "2021-01-27", "title": "$GME to the moon", "selftext": "", "score": 10},
        {"date": "2021-01-27", "title": "$GME calls printing tendies", "selftext": "", "score": 5},
        {"date": "2021-01-27", "title": "$GME is a scam, bagholders", "selftext": "", "score": 2},
    ])
    daily = build_daily_ticker_sentiment(add_sentiment(posts), {"GME"})
    row = daily.iloc[0]
    # 2 bullish + 1 bearish of 3 posts -> net_bullish exactly +1/3.
    assert row["n_posts"] == 3
    assert abs(row["net_bullish"] - 1 / 3) < 1e-9
    assert -1 <= row["avg_sentiment"] <= 1


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
    # weighted is deprecated (score**2 leak) - the column survives for
    # notebook compatibility but must always be 0 now.
    assert int(gold.keyword_weighted.iloc[0]) == 0


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
    assert int(row.inferred_weighted) == 0   # deprecated column, always 0 now


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
    assert list(counts.columns) == ["date", "ticker", "mention_count"]
    assert (counts["mention_count"] > 0).all()

    # Daytraders definitely talked about SPY at least once in 16 years.
    assert "SPY" in set(counts["ticker"])

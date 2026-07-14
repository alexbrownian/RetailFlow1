# find_emerging_terms.py
# ======================
# Find words and two-word phrases that are SPIKING in recent posts but are
# not covered by any existing theme - the early-warning system for trades
# the theme vocabulary does not know yet (e.g. "bearings" before a
# robotics supply-chain theme existed).
#
#     python helper/find_emerging_terms.py
#     python helper/find_emerging_terms.py --recent-days 14 --top 40
#
# WORKS ON BOTH MACHINES:
#   * external (personal): reads raw post text from posts.parquet
#   * internal (work):     reads ABSTRACTED_DATA/daily_term_counts.parquet,
#     the text-free daily term frequencies (built once externally via
#     build_term_counts.py, then kept current by every live fold)
# The mode is picked automatically from which file exists.
#
# HOW IT WORKS (simple ratio test):
#   1. Split history into RECENT (last N days) and BASELINE (the ~6 months
#      before that).
#   2. For each term, compute the share of posts mentioning it in each
#      period. Shares - not raw counts - so overall volume changes cancel.
#   3. Rank by recent_share / baseline_share (with +1 smoothing so
#      brand-new terms rank high without dividing by zero).
#   4. Drop terms already covered: theme keywords, known tickers, everyday
#      English (wordfreq zipf), boilerplate.

import argparse
import os
import sys
from collections import Counter

import pandas as pd

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, PROJECT_ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src.themes import THEME_KEYWORDS                                  # noqa: E402
from src.terms import terms_in_text, TOKEN_RE, TOTAL_MARKER            # noqa: E402
from src import abstracted_data                                        # noqa: E402

POSTS_PATH = os.path.join(PROJECT_ROOT, "data", "processed", "posts.parquet")
COUNTS_PATH = os.path.join(abstracted_data.ABSTRACTED_DIR,
                           "daily_term_counts.parquet")

MIN_RECENT_POSTS = 25    # a term must appear in at least this many recent posts
MAX_ZIPF = 4.4           # wordfreq zipf above this = everyday English, drop it
                         # ("bearings" ~3.9, "inflation" ~4.3, "market" ~5.4)
BASELINE_DAYS = 180

# --promote thresholds: a term this hot becomes a TRACKED auto-theme
# (counts + sentiment from the next fold). It stays OUT of trade signals
# until a human anchors it to an ETF - tracking is automatic, trading is not.
AUTO_RATIO = 10.0        # recent share must be >= 10x baseline share
AUTO_MIN_POSTS = 50      # and carried by at least this many recent posts
AUTO_MAX_PER_RUN = 3     # promotion budget per run - a meme week can't flood us


def covered_terms() -> set:
    """Every word already inside a theme keyword (single words AND the words
    inside phrases), lowercased - these are 'known', not emerging."""
    known = set()
    for keywords in THEME_KEYWORDS.values():
        for kw in keywords:
            for w in TOKEN_RE.findall(kw.lower()):
                known.add(w)
    return known


def known_tickers() -> set:
    """Ticker symbols (lowercased) from the reference universe files - a
    spiking ticker is picked up by the ticker pipeline already."""
    out = set()
    for fname in ("nasdaqlisted.txt", "otherlisted.txt"):
        path = os.path.join(PROJECT_ROOT, "data", "reference", fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                sym = line.split("|")[0].strip().lower()
                if sym and sym != "symbol":
                    out.add(sym)
    return out


def from_raw_posts(recent_days):
    """External machine: stream posts.parquet, count unique posts per term
    in the recent and baseline windows. Returns (recent Counter, base
    Counter, n_recent, n_base, cutoff, floor)."""
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(POSTS_PATH)

    newest = None
    for batch in pf.iter_batches(columns=["date"], batch_size=200_000):
        s = max(str(d)[:10] for d in batch.column("date").to_pylist())
        newest = s if newest is None or s > newest else newest
    newest_ts = pd.Timestamp(newest)
    cutoff = (newest_ts - pd.Timedelta(days=recent_days)).strftime("%Y-%m-%d")
    floor = (newest_ts - pd.Timedelta(days=recent_days + BASELINE_DAYS)
             ).strftime("%Y-%m-%d")

    recent, base = Counter(), Counter()
    n_recent = n_base = 0
    for batch in pf.iter_batches(columns=["date", "title", "selftext"],
                                 batch_size=100_000):
        dates = [str(d)[:10] for d in batch.column("date").to_pylist()]
        titles = batch.column("title").to_pylist()
        bodies = batch.column("selftext").to_pylist()
        for date, title, body in zip(dates, titles, bodies):
            if date < floor:
                continue
            terms = terms_in_text((title or "") + " " + (body or ""))
            if date >= cutoff:
                n_recent += 1
                recent.update(terms)
            else:
                n_base += 1
                base.update(terms)
    return recent, base, n_recent, n_base, cutoff, floor


def from_term_counts(recent_days):
    """Internal machine: the same four numbers, from the committed text-free
    counts file. __TOTAL__ rows carry each day's post count."""
    df = pd.read_parquet(COUNTS_PATH)
    df["date"] = pd.to_datetime(df["date"])
    newest = df["date"].max()
    cutoff = newest - pd.Timedelta(days=recent_days)
    floor = cutoff - pd.Timedelta(days=BASELINE_DAYS)
    df = df[df["date"] >= floor]

    is_recent = df["date"] >= cutoff
    totals = df[df["term"] == TOTAL_MARKER]
    n_recent = int(totals.loc[is_recent, "mention_count"].sum())
    n_base = int(totals.loc[~is_recent, "mention_count"].sum())

    terms = df[df["term"] != TOTAL_MARKER]
    recent = Counter(terms.loc[is_recent].groupby("term")["mention_count"]
                     .sum().to_dict())
    base = Counter(terms.loc[~is_recent].groupby("term")["mention_count"]
                   .sum().to_dict())
    return (recent, base, n_recent, n_base,
            cutoff.strftime("%Y-%m-%d"), floor.strftime("%Y-%m-%d"))


def promote(rows):
    """Append the hottest qualifying terms to data/reference/auto_themes.csv
    so they start being TRACKED (counted + scored) from the next fold.
    Skips terms already promoted. Returns the list of new theme names."""
    from src.themes import AUTO_THEMES_PATH, load_auto_themes

    already = set(load_auto_themes().keys())
    new_rows = []
    for term, n, _rs, _bs, ratio in rows:
        if len(new_rows) >= AUTO_MAX_PER_RUN:
            break
        if ratio < AUTO_RATIO or n < AUTO_MIN_POSTS:
            continue
        theme_name = "auto_" + term.replace(" ", "_")
        if theme_name in already:
            continue
        new_rows.append({"theme": theme_name, "keyword": term,
                         "first_seen": pd.Timestamp.today().strftime("%Y-%m-%d")})
    if not new_rows:
        return []

    old = (pd.read_csv(AUTO_THEMES_PATH)
           if os.path.exists(AUTO_THEMES_PATH)
           else pd.DataFrame(columns=["theme", "keyword", "first_seen"]))
    combined = pd.concat([old, pd.DataFrame(new_rows)], ignore_index=True)
    os.makedirs(os.path.dirname(AUTO_THEMES_PATH), exist_ok=True)
    tmp = AUTO_THEMES_PATH + ".tmp"
    combined.to_csv(tmp, index=False)
    os.replace(tmp, AUTO_THEMES_PATH)
    return [r["theme"] for r in new_rows]


def main():
    ap = argparse.ArgumentParser(description="Spot spiking terms no theme covers.")
    ap.add_argument("--recent-days", type=int, default=14,
                    help="the 'now' window to compare against history (default 14)")
    ap.add_argument("--top", type=int, default=30, help="how many terms to show")
    ap.add_argument("--promote", action="store_true",
                    help="auto-add the hottest qualifying terms as tracked "
                         "auto-themes (no ETF anchor until a human adds one)")
    args = ap.parse_args()

    if os.path.exists(POSTS_PATH):
        mode = "raw posts (external machine)"
        recent, base, n_recent, n_base, cutoff, floor = from_raw_posts(args.recent_days)
    elif os.path.exists(COUNTS_PATH):
        mode = "abstracted term counts (works on the internal machine)"
        recent, base, n_recent, n_base, cutoff, floor = from_term_counts(args.recent_days)
    else:
        print("neither posts.parquet nor ABSTRACTED_DATA/daily_term_counts.parquet"
              " exists.\nExternal machine: run data_ingestion/scripts/"
              "build_term_counts.py once, then commit ABSTRACTED_DATA.")
        return 1

    print(f"source: {mode}")
    print(f"recent = after {cutoff} | baseline = {floor} -> {cutoff}")
    if n_recent == 0 or n_base == 0:
        print("not enough posts on one side of the split - widen the windows.")
        return 1
    print(f"recent posts: {n_recent:,} | baseline posts: {n_base:,}")

    try:
        from wordfreq import zipf_frequency
    except ImportError:
        print("wordfreq not installed (pip install wordfreq) - "
              "common-English filtering disabled.")
        def zipf_frequency(word, lang):
            return 0.0

    known = covered_terms()
    tickers = known_tickers()

    rows = []
    for term, n in recent.items():
        if n < MIN_RECENT_POSTS:
            continue
        if " " in term:                     # two-word phrase
            w1, w2 = term.split(" ", 1)
            if w1 in known and w2 in known:
                continue
        else:                               # single word
            if term in known or term in tickers:
                continue
            if zipf_frequency(term, "en") > MAX_ZIPF:
                continue
        recent_share = n / n_recent
        base_share = base.get(term, 0) / n_base
        ratio = recent_share / ((base.get(term, 0) + 1) / n_base)
        rows.append((term, n, recent_share * 100, base_share * 100, ratio))
    rows.sort(key=lambda r: r[4], reverse=True)

    print(f"\nTOP {args.top} EMERGING TERMS (no theme covers these)")
    print(f"{'term':<28} {'recent posts':>12} {'recent %':>9} {'base %':>8} {'spike x':>8}")
    for label, n, rs, bs, ratio in rows[:args.top]:
        print(f"{label:<28} {n:>12,} {rs:>8.2f}% {bs:>7.2f}% {ratio:>7.1f}x")
    if not rows:
        print("(nothing above the thresholds - lower MIN_RECENT_POSTS to dig deeper)")

    if args.promote:
        promoted = promote(rows)
        if promoted:
            print(f"\nAUTO-PROMOTED {len(promoted)} new tracked theme(s): "
                  + ", ".join(promoted))
            print("they count + score from the next fold. To make one "
                  "TRADEABLE: give it an ETF in THEME_ETFS (src/themes.py), "
                  "or fold its keyword into a hand-written theme.")
            print("commit data/reference/auto_themes.csv so both machines see it.")
        else:
            print(f"\nno term met the promotion bar (>= {AUTO_RATIO:.0f}x spike "
                  f"and >= {AUTO_MIN_POSTS} recent posts).")
    else:
        print("\nnext step: promising terms become keywords of a new or existing "
              "theme in src/themes.py, then rerun update_data.py --full "
              "(external) so history reflects them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

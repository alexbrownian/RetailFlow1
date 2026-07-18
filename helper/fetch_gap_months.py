# fetch_gap_months.py
# ===================
# Fill a DATE GAP in the post history via the Arctic Shift API - no torrents,
# no 22 GB monthly dumps. Downloads ONLY your subreddits for ONLY the window
# you ask for (e.g. Jan-Jun 2026: a few hundred MB instead of ~130 GB).
#
#     python helper/fetch_gap_months.py
#
# For each subreddit it pages backwards through
#     https://arctic-shift.photon-reddit.com/api/posts/search
# writing the raw JSON lines (Pushshift-shaped - the same schema the dumps
# use) into data/raw/arctic_<sub>_submissions_<window>.zst, then appends
# everything into posts.parquet (dedup by id, as always).
#
# Polite by design: 100 posts per request, a pause between requests, and
# backoff on errors. A busy subreddit like wallstreetbets takes a while -
# leave it running. Re-running SKIPS subreddits whose file already exists.

import json
import os
import subprocess
import sys
import time

import requests
import zstandard

# ======================== EDIT THIS ========================
START = "2026-01-01"     # inclusive
END = "2026-07-01"       # exclusive (first day you already have live data)

SUBREDDITS = [
    "wallstreetbets", "CryptoCurrency", "personalfinance", "Bitcoin",
    "investing", "stocks", "pennystocks", "StockMarket", "options",
    "Daytrading", "financialindependence", "dividends", "thetagang",
    "SecurityAnalysis", "ValueInvesting", "finance", "Bogleheads",
]

PAUSE_S = 0.3            # polite gap between requests
APPEND_TO_MASTER = True  # fold the downloads into posts.parquet at the end
# ===========================================================

API = "https://arctic-shift.photon-reddit.com/api/posts/search"
PAGE = 100               # posts per request (API maximum)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw")


def fetch_page(sub, after, before, retries=5):
    """One API call -> list of raw post dicts (newest first). Retries with
    growing pauses on rate limits / hiccups."""
    for attempt in range(retries):
        try:
            r = requests.get(API, params={"subreddit": sub, "after": after,
                                          "before": before, "limit": PAGE},
                             timeout=(10, 60))
            if r.status_code == 200:
                return r.json().get("data", [])
            print(f"    HTTP {r.status_code} - backing off "
                  f"({30 * (attempt + 1)}s)...")
        except requests.RequestException as e:
            print(f"    network hiccup ({e}) - retrying...")
        time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"API kept failing for r/{sub} - try again later")


def fetch_subreddit(sub):
    """All submissions of one subreddit in [START, END) -> one .zst file of
    raw JSON lines (exactly the shape the Pushshift dumps use).

    CRASH/WIFI-SAFE RESUME: pages are written to an UNCOMPRESSED .ndjson.tmp
    and the paging cursor is saved to a .cursor file after every page. If
    the process dies (wifi cut, laptop closed, Ctrl+C), rerunning picks up
    from the exact page it stopped at - nothing refetched but at most one
    page (whose duplicates the append step dedups by id anyway). Only when
    a subreddit COMPLETES is the temp compressed into the final .zst and
    the temp files removed."""
    out_name = f"arctic_{sub}_submissions_{START}_{END}.zst"
    out_path = os.path.join(RAW_DIR, out_name)
    if os.path.exists(out_path):
        print(f"r/{sub}: {out_name} already exists - skipped (delete to refetch)")
        return out_path

    tmp = out_path + ".ndjson.tmp"       # plain text while in flight
    cursor_file = out_path + ".cursor"
    before = END
    total = 0
    if os.path.exists(tmp) and os.path.exists(cursor_file):
        before = open(cursor_file).read().strip()
        total = sum(1 for _ in open(tmp, encoding="utf-8"))
        print(f"  r/{sub}: RESUMING - {total:,} posts already on disk, "
              f"continuing from cursor {before}")

    f = open(tmp, "a", encoding="utf-8")
    while True:
        rows = fetch_page(sub, START, before)
        if not rows:
            break
        for rec in rows:
            f.write(json.dumps(rec) + "\n")
        f.flush()
        total += len(rows)
        oldest = min(int(r["created_utc"]) for r in rows)
        # save the cursor ATOMICALLY after the rows are safely on disk
        with open(cursor_file + ".new", "w") as cf:
            cf.write(str(oldest))
        os.replace(cursor_file + ".new", cursor_file)
        print(f"  r/{sub}: {total:,} posts so far "
              f"(back to {time.strftime('%Y-%m-%d', time.gmtime(oldest))})",
              flush=True)
        if len(rows) < PAGE:
            break                    # last (partial) page
        before = str(oldest)         # next page: strictly older posts
        time.sleep(PAUSE_S)
    f.close()

    # subreddit COMPLETE: compress to the final .zst, clean the temps
    with open(tmp, "rb") as src, open(out_path + ".ztmp", "wb") as dst:
        zstandard.ZstdCompressor().copy_stream(src, dst)
    os.replace(out_path + ".ztmp", out_path)
    os.remove(tmp)
    os.remove(cursor_file)
    print(f"r/{sub}: DONE - {total:,} posts -> {out_name}")
    return out_path


def fold_abstracted(files):
    """HP path (no posts.parquet here): fold the downloaded posts
    STRAIGHT into the text-free ABSTRACTED_DATA aggregates, exactly like a
    live pull does. A gap ledger of folded post ids makes re-runs harmless
    (a post folds once, ever). The raw .zst files stay in data/raw/ - copy
    them to the XPS's data/raw/ when convenient, otherwise the
    next --full there (which rebuilds from ITS master) would revert these
    months in the aggregates."""
    import io
    import json as _json

    sys.path.insert(0, ROOT)
    from src import abstracted_data
    from src.clean_data import normalise

    ledger_path = os.path.join(ROOT, "data", "reference", "gap_folded_ids.json")
    seen = set()
    if os.path.exists(ledger_path):
        seen = set(_json.load(open(ledger_path, encoding="utf-8")))

    total = 0
    for fp in files:
        rows = []
        with open(fp, "rb") as fh:
            reader = zstandard.ZstdDecompressor().stream_reader(fh)
            for line in io.TextIOWrapper(reader, encoding="utf-8"):
                if not line.strip():
                    continue
                post = normalise(json.loads(line))
                if post["id"] in seen:
                    continue
                seen.add(post["id"])
                post["source"] = "reddit"
                rows.append(post)
        if not rows:
            print(f"{os.path.basename(fp)}: nothing new (all folded before)")
            continue
        import pandas as pd
        df = pd.DataFrame(rows)
        print(f"{os.path.basename(fp)}: folding {len(df):,} new posts...")
        aggs = abstracted_data.aggregate_posts(df)
        abstracted_data.merge_into_abstracted(aggs, verbose=False)
        total += len(df)
        # save the ledger after EVERY file, atomically - a crash never
        # causes a double-fold
        with open(ledger_path + ".tmp", "w", encoding="utf-8") as lf:
            _json.dump(sorted(seen), lf)
        os.replace(ledger_path + ".tmp", ledger_path)

    abstracted_data.hydrate(verbose=False)   # notebooks read data/processed
    print(f"\nfolded {total:,} posts into ABSTRACTED_DATA (text-free).")
    print("next: run notebooks 08/09/10 (any update_data run does it), then")
    print("commit + push ABSTRACTED_DATA.")
    print("NOTE: keep the arctic_*.zst files and copy them to the XPS's")
    print("data/raw/ eventually - a --full there rebuilds from its")
    print("posts.parquet and would otherwise revert these months.")
    return 0


def main():
    # --live N : ignore the EDIT-THIS window and pull the last N days up to
    # NOW - Arctic Shift archives Reddit within minutes, so this doubles as
    # a live Reddit source (complete coverage, no credits). Overlap with
    # previous pulls is harmless: ids dedup, filenames carry the window.
    global START, END
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", type=int, metavar="DAYS", default=0,
                    help="pull the last N days up to now instead of the "
                         "configured window")
    args = ap.parse_args()
    if args.live:
        import datetime as _dt
        today = _dt.date.today()
        START = (today - _dt.timedelta(days=args.live)).isoformat()
        END = (today + _dt.timedelta(days=1)).isoformat()

    os.makedirs(RAW_DIR, exist_ok=True)
    print(f"Arctic Shift {'LIVE' if args.live else 'gap'} fetch: "
          f"{len(SUBREDDITS)} subreddits, {START} -> {END}")
    files = []
    for sub in SUBREDDITS:
        files.append(fetch_subreddit(sub))
        time.sleep(PAUSE_S)

    if not APPEND_TO_MASTER:
        print("downloads complete (APPEND_TO_MASTER=False - fold manually)")
        return 0

    master = os.path.join(ROOT, "data", "processed", "posts.parquet")
    if os.path.exists(master):
        # XPS: the raw master exists - append into it
        print("\nappending everything into posts.parquet (dedup by id)...")
        code = subprocess.call(
            [sys.executable,
             os.path.join(ROOT, "data_ingestion", "scripts", "prep_posts.py"),
             "--append"] + files)
        if code != 0:
            print("append FAILED - downloads are safe in data/raw/; retry with:")
            print("  python data_ingestion/scripts/prep_posts.py --append "
                  + " ".join(files))
            return code
        print("next: python update_data.py --full  (aggregates over the new months)")
        return 0

    # HP: no master here - fold text-free into ABSTRACTED_DATA
    print("\nno posts.parquet on this machine -> folding straight into "
          "ABSTRACTED_DATA (HP mode)")
    return fold_abstracted(files)


if __name__ == "__main__":
    raise SystemExit(main())

# fetch_x_live.py
# ===============
# LIVE X (Twitter) ingestion via the OFFICIAL v2 API - built now, armed
# later: it does nothing until you put a bearer token in .env. The moment
# you pay for API access and set the token, the whole pipeline lights up
# with ZERO other changes:
#
#     .env:  X_BEARER_TOKEN = AAAA....           (from developer.x.com)
#     python api_calls/fetch_x_live.py                    (on a schedule)
#     python data_ingestion/scripts/add_x_data.py         (rebuilds X block)
#
# It appends tweets to data/raw/X Data/x_api_live.csv.zst - which is a
# registered dataset in src/x_data.py (normalise_x_api), so add_x_data.py
# merges it into posts.parquet exactly like the historical dumps. Real
# tweet ids share the 'x_' prefix with the dumps, so overlaps dedupe
# automatically (first seen wins).
#
# WHAT IT QUERIES: the v2 recent-search endpoint (last 7 days) with a
# cashtag query built from your theme anchors + retail favourites, English,
# retweets excluded. Edit build_query() to taste - queries are limited to
# ~512 chars on the Basic tier, so symbols are chunked into several calls.
#
# QUOTA PROTECTION (important - read tiers on developer.x.com/en/portal):
#   - Basic tier reads are capped per MONTH (order of 10-15k posts) and
#     ~60 requests / 15 min. MAX_TWEETS_PER_RUN below is your budget seat
#     belt: e.g. cap 300/run x 24 runs/day ~ 7.2k/day -> TOO MUCH for
#     Basic. Do the maths for your tier and cadence BEFORE scheduling:
#     Basic ~ run 2-3x/day with a 150-tweet cap; Pro ~ hourly is fine.
#   - On HTTP 429 the script stops immediately; the next run catches up.
#
# Run: python api_calls/fetch_x_live.py [--max-tweets 300]

import argparse
import io
import os
import sys
import time

import pandas as pd
import requests
import zstandard

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, PROJECT_ROOT)

from src.themes import THEME_ETFS  # noqa: E402

OUT_FILE = os.path.join(PROJECT_ROOT, "data", "raw", "X Data", "x_api_live.csv.zst")
SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
PAGE_SIZE = 100          # max_results per request (10-100)
PAUSE_S = 3.0            # polite gap between requests


def get_token() -> str | None:
    """os.environ first, then .env read by hand (no python-dotenv needed)."""
    token = os.environ.get("X_BEARER_TOKEN", "")
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not token and os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == "X_BEARER_TOKEN" and v.strip():
                    token = v.strip()
    return token or None


def build_queries() -> list[str]:
    """Cashtag queries chunked to stay under the ~512-char query limit."""
    core = {"GME", "AMC", "NVDA", "TSLA", "AAPL", "PLTR", "COIN", "MSTR", "SMCI"}
    symbols = sorted(set(THEME_ETFS.values()) | core)
    queries, chunk = [], []
    for sym in symbols:
        chunk.append(f"${sym}")
        if len(chunk) == 12:                     # ~12 cashtags per query
            queries.append(f"({' OR '.join(chunk)}) lang:en -is:retweet")
            chunk = []
    if chunk:
        queries.append(f"({' OR '.join(chunk)}) lang:en -is:retweet")
    return queries


def fetch(token: str, max_tweets: int) -> list[dict]:
    """Pull recent tweets across the chunked queries, stopping at the
    budget cap or the first 429."""
    headers = {"Authorization": f"Bearer {token}"}
    rows = []
    for query in build_queries():
        if len(rows) >= max_tweets:
            break
        params = {
            "query": query,
            "max_results": min(PAGE_SIZE, max(10, max_tweets - len(rows))),
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "username",
        }
        r = requests.get(SEARCH_URL, headers=headers, params=params, timeout=20)
        if r.status_code == 429:
            print("[stop] rate limited (429) - ending this run; next run catches up.")
            break
        if r.status_code != 200:
            print(f"[warn] query failed ({r.status_code}): {r.text[:120]}")
            continue
        payload = r.json()
        users = {u["id"]: u.get("username", "")
                 for u in payload.get("includes", {}).get("users", [])}
        for t in payload.get("data", []):
            rows.append({
                "id": t["id"],
                "created_at": t.get("created_at", ""),
                "text": t.get("text", ""),
                "author": users.get(t.get("author_id"), ""),
                "likes": (t.get("public_metrics") or {}).get("like_count", 0),
            })
        time.sleep(PAUSE_S)
    return rows


def append_to_raw(rows: list[dict]) -> None:
    """Append new tweets to the registered raw file, deduping on id."""
    new = pd.DataFrame(rows)
    if os.path.exists(OUT_FILE):
        old_bytes = zstandard.ZstdDecompressor().decompress(open(OUT_FILE, "rb").read())
        old = pd.read_csv(io.BytesIO(old_bytes), dtype={"id": str})
        new = pd.concat([old, new.astype({"id": str})], ignore_index=True)
    new = new.drop_duplicates(subset="id", keep="first")
    buf = new.to_csv(index=False).encode("utf-8")
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "wb") as f:
        f.write(zstandard.ZstdCompressor(level=10).compress(buf))
    print(f"raw file now holds {len(new):,} unique tweets -> {OUT_FILE}")


def main():
    p = argparse.ArgumentParser(description="Live X ingestion (official v2 API)")
    p.add_argument("--max-tweets", type=int, default=300,
                   help="budget cap per run - protect your monthly read quota")
    args = p.parse_args()

    token = get_token()
    if not token:
        print("X live ingestion is ARMED but OFF: no X_BEARER_TOKEN in .env.")
        print("When you pay for API access (developer.x.com), paste the")
        print("bearer token into .env and re-run - nothing else changes.")
        return 0

    rows = fetch(token, args.max_tweets)
    if not rows:
        print("no tweets fetched this run")
        return 0
    append_to_raw(rows)
    print("next: python data_ingestion/scripts/add_x_data.py  (merges into posts.parquet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

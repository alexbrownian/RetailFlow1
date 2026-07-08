# fetch_x_live.py
# ===============
# LIVE X (Twitter) ingestion with TWO interchangeable backends - whichever
# has a key in .env gets used (FetchLayer preferred if both are present):
#
#   A) FETCHLAYER (fetchlayer.dev - third-party structured social API)
#        .env:  FETCHLAYER_KEY = ss-...           (the SAME key as Reddit)
#        One POST per cashtag-chunk to /api/twitter/search, product=Latest.
#        Billing: 1 credit per REQUEST (same pool as the Reddit fetcher).
#        NO paid X developer account needed - this is the path that works
#        today when you only have a FetchLayer key.
#   B) OFFICIAL X v2 API (needs a PAID developer account)
#        .env:  X_BEARER_TOKEN = AAAA....         (from developer.x.com)
#        v2 recent-search (last 7 days). Armed but off until you pay.
#
#   python api_calls/fetch_x_live.py --test   # ONE small call, writes nothing
#   python api_calls/fetch_x_live.py          # real poll (fetch_all calls this)
#   python api_calls/fetch_x_live.py --max-tweets 150
#
# OUTPUT (both backends): data/raw/X Data/x_api_live.csv.zst
#   a flat csv (id, created_at, text, author, likes) - a REGISTERED dataset
#   (x_api_live in src/x_data.py, normalise_x_api). Real tweet ids share the
#   'x_' prefix with the historical dumps, so overlaps dedupe automatically
#   (first seen wins). The merge into posts.parquet is done by
#   data_ingestion/scripts/merge_live.py (update_data.py calls it).
#
# QUOTA NOTES:
#   FetchLayer - 1 credit per chunk-request; the default symbol list is a
#     few chunks per run, so a run costs a handful of credits.
#   Official   - reads capped per MONTH and ~60 req/15min; --max-tweets is
#     your seat belt. Both backends stop instantly on HTTP 429/402.

import argparse
import datetime
import io
import os
import re
import sys
import time

import pandas as pd
import requests
import zstandard

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, PROJECT_ROOT)

try:                     # tweets contain emoji/links; don't die on cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src.themes import THEME_ETFS  # noqa: E402

OUT_FILE = os.path.join(PROJECT_ROOT, "data", "raw", "X Data", "x_api_live.csv.zst")
FETCHLAYER_URL = "https://fetchlayer.dev/api/twitter/search"
SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
PAGE_SIZE = 100          # max_results per request (10-100)
PAUSE_S = 3.0            # polite gap between requests
STATUS_ID = re.compile(r"/status/(\d+)")


def load_env():
    """Read keys from .env DIRECTLY (no python-dotenv), os.environ fallback.
    Accepts FETCHLAYER_KEY or FETCHLAYER_API_KEY (same key the Reddit
    fetcher uses)."""
    from_file = {}
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                from_file[k.strip()] = v.strip()

    def get(*names):
        for name in names:
            v = from_file.get(name) or os.environ.get(name, "")
            if v.strip():
                return v.strip()
        return ""

    return {"FETCHLAYER_API_KEY": get("FETCHLAYER_KEY", "FETCHLAYER_API_KEY"),
            "X_BEARER_TOKEN": get("X_BEARER_TOKEN")}


def build_queries():
    """Cashtag queries chunked to stay well under any query-length limit."""
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


# ---------------- backend A: FetchLayer ----------------
def _fl_row(t):
    """Map ONE FetchLayer tweet object to our flat raw schema. FetchLayer's
    exact field names can shift, so every field is looked up defensively.
    Returns None if the tweet has no usable numeric status id."""
    url = t.get("url") or t.get("tweetUrl") or ""
    tweet_id = ""
    m = STATUS_ID.search(url) if isinstance(url, str) else None
    if m:
        tweet_id = m.group(1)
    else:
        for key in ("id", "tweetId", "id_str", "restId"):
            if str(t.get(key, "")).isdigit():
                tweet_id = str(t[key])
                break
    if not tweet_id:
        return None
    author = t.get("author") or {}
    handle = (author.get("handle") or author.get("username") or author.get("screenName")
              if isinstance(author, dict) else author) or ""
    created = (t.get("createdAt") or t.get("created_at") or t.get("date")
               or t.get("time") or "")
    if not created:                              # "Latest" => essentially now
        created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # Engagement lives either at the top level or inside a "counts" object.
    counts = t.get("counts") if isinstance(t.get("counts"), dict) else {}
    likes = (t.get("likeCount") or t.get("likes") or t.get("favoriteCount")
             or counts.get("likes") or counts.get("favorites")
             or counts.get("likeCount") or 0)
    return {
        "id": tweet_id,
        "created_at": created,
        "text": t.get("text") or t.get("fullText") or t.get("content") or "",
        "author": str(handle),
        "likes": likes,
    }


def fetchlayer_search(key, query, count):
    r = requests.post(FETCHLAYER_URL,
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      json={"query": query, "product": "Latest", "count": count},
                      timeout=30)
    return r


def fetchlayer_test(key):
    """ONE call, prints what came back, writes nothing (a few credits)."""
    query = "($NVDA OR $TSLA OR $GME) lang:en -is:retweet"
    print(f"POST twitter/search(product=Latest, count=5)\n  query: {query}")
    r = fetchlayer_search(key, query, 5)
    print(f"-> HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:300])
        return 1
    payload = r.json()
    results = (payload.get("results") or payload.ge
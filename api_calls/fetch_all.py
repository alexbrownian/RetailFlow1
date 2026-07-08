# fetch_all.py
# ============
# THE ONE FILE for all live API calls. It has TWO clearly separated modes:
#
#   TESTING MODE   python api_calls/fetch_all.py --test
#       Makes EXACTLY ONE FetchLayer call, PRINTS what came back, and writes
#       NOTHING (no raw files, no parquet change). Use it to prove your key
#       works and eyeball the data. Pick the endpoint with --source:
#           --test                 (Reddit: r/wallstreetbets newest 5)
#           --test --source x      (X: newest tweets for a few cashtags)
#
#   NORMAL MODE    python api_calls/fetch_all.py
#       1. CHECKS .env - a source with its key filled is CALLED; a source
#          with an empty key is SKIPPED (no request sent).
#       2. CALLS every enabled fetcher (writes raw files).
#       3. APPENDS the new posts - destination picked AUTOMATICALLY:
#            * producer machine (posts.parquet exists) -> merge_live.py appends
#              the raw posts into posts.parquet ("first seen wins").
#            * work laptop (no posts.parquet, or --gic) -> append_live_to_gic.py
#              folds them into GIC_RAW_DATA as text-free aggregates.
#          Skip the append entirely with --no-merge.
#
#   Other flags:
#       --check     print the .env key check ONLY, call nothing
#       --no-merge  NORMAL mode but stop after writing raw (no append)
#       --gic       force the GIC_RAW_DATA append (the work-laptop path)
#
# Sources and their keys (.env at the project root):
#   StockTwits : no key needed          - always called
#   Reddit     : FETCHLAYER_KEY         (or official REDDIT_* keys)
#   X          : FETCHLAYER_KEY         (or official X_BEARER_TOKEN)
#   -> a single FetchLayer key lights up BOTH Reddit and X.
#
# update_data.py calls this file (with --no-merge) then merges once itself.
# After a run, see what landed:  python check_live_ingestion.py

import argparse
import os
import subprocess
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)


def read_env():
    """Read .env by hand (no python-dotenv needed). Values never printed."""
    keys = {}
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
    return keys


def fetch_plan():
    """THE CHECK. Returns [(source, will_call, reason, script_args)]."""
    keys = read_env()

    def have(*names):                       # is ANY of these keys filled?
        return any(keys.get(n, "").strip() for n in names)

    fetchlayer = have("FETCHLAYER_KEY", "FETCHLAYER_API_KEY")

    reddit_ok = fetchlayer or (have("REDDIT_PERSONAL_USE") and have("REDDIT_SECRET"))
    reddit_why = ("FetchLayer key found" if fetchlayer
                  else "official Reddit keys found" if reddit_ok
                  else "no FETCHLAYER_KEY / REDDIT_* keys")

    x_ok = fetchlayer or have("X_BEARER_TOKEN")
    x_why = ("FetchLayer key found" if fetchlayer
             else "official X_BEARER_TOKEN found" if x_ok
             else "no FETCHLAYER_KEY / X_BEARER_TOKEN")

    return [
        ("StockTwits", True, "public API - no key needed", ["fetch_stocktwits.py"]),
        ("Reddit", reddit_ok, reddit_why, ["fetch_reddit_live.py"]),
        ("X", x_ok, x_why, ["fetch_x_live.py"]),
    ]


def run_script(script_args, extra=None):
    return subprocess.run([sys.executable, os.path.join(THIS_DIR, script_args[0]),
                           *script_args[1:], *(extra or [])],
                          cwd=PROJECT_ROOT).returncode


def run_test(source):
    """TESTING MODE: exactly one FetchLayer call, prints output, writes nothing."""
    print("=" * 60)
    print(f"TESTING MODE - one FetchLayer call ({source}),
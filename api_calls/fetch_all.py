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
# run_daily.py calls this file (with --no-merge) then merges once itself.
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
    print(f"TESTING MODE - one FetchLayer call ({source}), nothing is written")
    print("=" * 60)
    if source == "x":
        return run_script(["fetch_x_live.py", "--test"])
    return run_script(["test_fetchlayer.py"])       # Reddit, 1 credit


def main():
    p = argparse.ArgumentParser(description="Live API calls: --test (1 call) or normal (fetch + append)")
    p.add_argument("--test", action="store_true",
                   help="TESTING MODE: ONE FetchLayer call, print it, write nothing")
    p.add_argument("--source", choices=["reddit", "x"], default="reddit",
                   help="which endpoint --test hits (default: reddit)")
    p.add_argument("--check", action="store_true",
                   help="print the .env key check only - make no API calls")
    p.add_argument("--no-merge", action="store_true",
                   help="NORMAL mode but skip the parquet append (raw files only)")
    p.add_argument("--gic", action="store_true",
                   help="fold new posts into GIC_RAW_DATA (text-free aggregates) "
                        "instead of posts.parquet - the work-laptop path")
    args = p.parse_args()

    # ---- TESTING MODE ---------------------------------------------------
    if args.test:
        return run_test(args.source)

    # ---- NORMAL MODE ----------------------------------------------------
    plan = fetch_plan()
    print("NORMAL MODE - API key check (.env at project root):")
    for name, will_call, why, _ in plan:
        print(f"  {'CALL' if will_call else 'SKIP'}  {name:<10} ({why})")
    if args.check:
        print("\n--check: no calls made. Run without --check to fetch.")
        return 0

    print()
    failed = []
    for name, will_call, _, script_args in plan:
        if not will_call:
            continue
        print(f"--- {name} ---")
        if run_script(script_args) != 0:
            failed.append(name)

    print("\nfetch done." + (f" FAILED: {', '.join(failed)}" if failed else " all sources ok."))

    # ---- APPEND the fresh raw ------------------------------------------
    # TWO possible destinations, picked automatically:
    #   * posts.parquet  (producer machine - the 1 GB raw store exists)
    #       -> data_ingestion/scripts/merge_live.py
    #   * GIC_RAW_DATA   (work laptop - no raw store allowed; --gic forces this,
    #       and it is also chosen automatically when posts.parquet is absent)
    #       -> api_calls/append_live_to_gic.py  (text-free aggregates)
    posts_path = os.path.join(PROJECT_ROOT, "data", "processed", "posts.parquet")
    use_gic = args.gic or not os.path.exists(posts_path)

    if args.no_merge:
        target = "GIC_RAW_DATA" if use_gic else "posts.parquet"
        later = ("api_calls/append_live_to_gic.py" if use_gic
                 else "data_ingestion/scripts/merge_live.py")
        print(f"--no-merge: raw written, {target} NOT touched. "
              f"To append later:  python {later}")
    elif use_gic:
        why = "--gic" if args.gic else "no posts.parquet found (work-laptop mode)"
        print(f"\n--- APPEND: folding new posts into GIC_RAW_DATA ({why}) ---")
        rc = subprocess.run(
            [sys.executable, os.path.join(THIS_DIR, "append_live_to_gic.py")],
            cwd=PROJECT_ROOT).returncode
        if rc != 0:
            failed.append("gic-append")
    else:
        print("\n--- MERGE: appending new posts into posts.parquet ---")
        merge_rc = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "data_ingestion", "scripts", "merge_live.py")],
            cwd=PROJECT_ROOT).returncode
        if merge_rc != 0:
            failed.append("merge")

    print("\nsee what landed:  python check_live_ingestion.py")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

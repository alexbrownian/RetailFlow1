# fetch_all.py
# ============
# The single entry point for all live API calls, with two modes:
#
#   TESTING MODE   python api_calls/fetch_all.py --test
#       Makes exactly ONE FetchLayer call, prints what came back, writes
#       nothing. Verifies the key works. Pick the endpoint with --source:
#           --test                 (Reddit: r/wallstreetbets newest 5)
#           --test --source x      (X: newest tweets for a few cashtags)
#
#   NORMAL MODE    python api_calls/fetch_all.py
#       1. Checks .env - a source with its key filled is CALLED; a source
#          with an empty key is SKIPPED (no request sent).
#       2. Calls every enabled fetcher (writes raw files).
#       3. Appends the new posts - destination picked automatically:
#            * external machine (posts.parquet exists) -> merge_live.py
#              appends the raw posts into posts.parquet (first seen wins)
#            * internal machine (no posts.parquet, or --abstracted) ->
#              append_live_abstracted.py folds them into ABSTRACTED_DATA
#              as text-free aggregates
#          Skip the append entirely with --no-merge.
#
#   Other flags:
#       --check       print the .env key check only, call nothing
#       --no-merge    NORMAL mode but stop after writing raw (no append)
#       --abstracted  force the ABSTRACTED_DATA append (internal-machine path)
#
# Sources and their keys (.env at the project root):
#   StockTwits : no key needed          - always called
#   Reddit     : FETCHLAYER_KEY         (or official REDDIT_* keys)
#   X          : FETCHLAYER_KEY         (or official X_BEARER_TOKEN)
#   -> a single FetchLayer key enables BOTH Reddit and X.
#
# update_data.py calls this file (with --no-merge) then merges once itself.
# After a run:  python check_live_ingestion.py  shows what landed.

import argparse
import os
import subprocess
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)


def read_env():
    """Read .env directly (no dependency). Values are never printed."""
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
        # Reddit now comes from Arctic Shift: complete per-subreddit
        # coverage, near-real-time, free (no key). FetchLayer stays for X.
        # fetch_reddit_live.py remains available as a manual fallback.
        ("Reddit", True, "Arctic Shift public API - no key needed",
         ["fetch_reddit_arctic.py"]),
        ("X", x_ok, x_why, ["fetch_x_live.py"]),
    ]


def run_script(script_args, extra=None):
    return subprocess.run([sys.executable, os.path.join(THIS_DIR, script_args[0]),
                           *script_args[1:], *(extra or [])],
                          cwd=PROJECT_ROOT).returncode


def run_test(source):
    """TESTING MODE: one FetchLayer call, prints output, writes nothing."""
    print("=" * 60)
    print(f"TESTING MODE - one FetchLayer call ({source}), nothing is written")
    print("=" * 60)
    if source == "x":
        return run_script(["fetch_x_live.py", "--test"])
    return run_script(["test_fetchlayer.py"])       # Reddit, 1 credit


def main():
    p = argparse.ArgumentParser(description="Live API calls: --test (1 call) or normal (fetch + append)")
    p.add_argument("--test", action="store_true",
                   help="TESTING MODE: one FetchLayer call, print it, write nothing")
    p.add_argument("--source", choices=["reddit", "x"], default="reddit",
                   help="which endpoint --test hits (default: reddit)")
    p.add_argument("--check", action="store_true",
                   help="print the .env key check only - make no API calls")
    p.add_argument("--no-merge", action="store_true",
                   help="NORMAL mode but skip the parquet append (raw files only)")
    p.add_argument("--abstracted", action="store_true",
                   help="fold new posts into ABSTRACTED_DATA (text-free "
                        "aggregates) instead of posts.parquet - the "
                        "internal-machine path")
    p.add_argument("--lookback-days", type=int, default=7,
                   help="how far back the fetch reaches (top posts of the "
                        "last N days); overlap never duplicates")
    p.add_argument("--max-credits", type=int, default=60,
                   help="FetchLayer credit cap per source per run")
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
    # the two knobs travel to every fetcher that understands them
    knobs = {"fetch_reddit_arctic.py": ["--lookback-days", str(args.lookback_days)],
             "fetch_reddit_live.py": ["--lookback-days", str(args.lookback_days),
                                      "--max-credits", str(args.max_credits)],
             "fetch_x_live.py": ["--lookback-days", str(args.lookback_days),
                                 "--max-credits", str(args.max_credits)]}
    for name, will_call, _, script_args in plan:
        if not will_call:
            continue
        print(f"--- {name} ---")
        if run_script(script_args, extra=knobs.get(script_args[0])) != 0:
            failed.append(name)

    print("\nfetch done." + (f" FAILED: {', '.join(failed)}" if failed else " all sources ok."))

    # ---- APPEND the fresh raw ------------------------------------------
    # Two possible destinations, picked automatically:
    #   * posts.parquet   (external machine - the raw store exists)
    #       -> data_ingestion/scripts/merge_live.py
    #   * ABSTRACTED_DATA (internal machine - no raw store allowed;
    #       --abstracted forces this, and it is also chosen automatically
    #       when posts.parquet is absent)
    #       -> api_calls/append_live_abstracted.py
    posts_path = os.path.join(PROJECT_ROOT, "data", "processed", "posts.parquet")
    use_abstracted = args.abstracted or not os.path.exists(posts_path)

    if args.no_merge:
        target = "ABSTRACTED_DATA" if use_abstracted else "posts.parquet"
        later = ("api_calls/append_live_abstracted.py" if use_abstracted
                 else "data_ingestion/scripts/merge_live.py")
        print(f"--no-merge: raw written, {target} NOT touched. "
              f"To append later:  python {later}")
    elif use_abstracted:
        why = "--abstracted" if args.abstracted else "no posts.parquet found (internal machine)"
        print(f"\n--- APPEND: folding new posts into ABSTRACTED_DATA ({why}) ---")
        rc = subprocess.run(
            [sys.executable, os.path.join(THIS_DIR, "append_live_abstracted.py")],
            cwd=PROJECT_ROOT).returncode
        if rc != 0:
            failed.append("abstracted-append")
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

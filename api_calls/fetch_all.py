# fetch_all.py
# ============
# THE ONE FILE for all live API calls. Run it and it:
#   1. CHECKS .env - for each source, is its key filled?
#        filled -> the source is called this run
#        empty  -> the source is ignored completely (no request sent)
#   2. CALLS every enabled fetcher (they live in this same folder)
#
#   python api_calls/fetch_all.py           # check keys, then fetch
#   python api_calls/fetch_all.py --check   # print the check ONLY, call nothing
#   python api_calls/fetch_all.py --test    # 1-credit FetchLayer key test only
#
# Sources and their keys (.env at the project root):
#   StockTwits : no key needed - always called
#   Reddit     : FETCHLAYER_KEY (fetchlayer.dev)  OR  official REDDIT_* keys
#   X          : X_BEARER_TOKEN (developer.x.com)
#
# run_daily.py calls this file for its fetch stage - same code either way.
# After fetching, see what landed:  python check_live_ingestion.py

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
    """THE CHECK. Returns [(source, will_call, reason, script_filename)]."""
    keys = read_env()

    def have(*names):                       # is ANY of these keys filled?
        return any(keys.get(n, "").strip() for n in names)

    reddit_ok = have("FETCHLAYER_KEY", "FETCHLAYER_API_KEY") \
        or (have("REDDIT_PERSONAL_USE") and have("REDDIT_SECRET"))
    reddit_why = ("FetchLayer key found" if have("FETCHLAYER_KEY", "FETCHLAYER_API_KEY")
                  else "official Reddit keys found" if reddit_ok
                  else "no FETCHLAYER_KEY / REDDIT_* keys")
    return [
        ("StockTwits", True, "public API - no key needed", "fetch_stocktwits.py"),
        ("Reddit", reddit_ok, reddit_why, "fetch_reddit_live.py"),
        ("X", have("X_BEARER_TOKEN"),
         "X_BEARER_TOKEN found" if have("X_BEARER_TOKEN") else "X_BEARER_TOKEN empty",
         "fetch_x_live.py"),
    ]


def main():
    p = argparse.ArgumentParser(description="Check keys, then call every enabled API")
    p.add_argument("--check", action="store_true",
                   help="print the key check only - make no API calls")
    p.add_argument("--test", action="store_true",
                   help="run the 1-credit FetchLayer key test only")
    args = p.parse_args()

    if args.test:
        r = subprocess.run([sys.executable,
                            os.path.join(THIS_DIR, "test_fetchlayer.py")],
                           cwd=PROJECT_ROOT)
        return r.returncode

    plan = fetch_plan()
    print("API key check (.env at project root):")
    for name, will_call, why, _ in plan:
        print(f"  {'CALL' if will_call else 'SKIP'}  {name:<10} ({why})")
    if args.check:
        print("\n--check: no calls made. Run without --check to fetch.")
        return 0

    print()
    failed = []
    for name, will_call, _, script in plan:
        if not will_call:
            continue
        print(f"--- {name} ---")
        r = subprocess.run([sys.executable, os.path.join(THIS_DIR, script)],
                           cwd=PROJECT_ROOT)
        if r.returncode != 0:
            failed.append(name)

    print("\ndone." + (f" FAILED: {', '.join(failed)}" if failed else " all sources ok."))
    print("see what landed:  python check_live_ingestion.py")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

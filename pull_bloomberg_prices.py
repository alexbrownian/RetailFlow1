#!/usr/bin/env python
"""
pull_bloomberg_prices.py - daily close prices from the Bloomberg Terminal API.

Run this on the WORK LAPTOP (the one with the Terminal + blpapi installed),
AFTER update_data.py, so the tickers it pulls match what your notebooks show:

    python pull_bloomberg_prices.py            # connect + pull + save
    python pull_bloomberg_prices.py --dry-run  # show what it WOULD pull, no connect

WHAT IT PULLS
    Field   : PX_LAST (daily close)
    Window  : START_DATE -> END_DATE from update_data.py ('' end = up to today)
    Symbols : the union of
                * the PRICE_TOP_N most-mentioned tickers over the window
                  (from data/processed/daily_ticker_counts.parquet)
                * every theme's ETF (src/themes.py THEME_ETFS)
                * anything that appears in the signals
                  (trade_signals.etf + trade_signals_tickers.ticker)
              Each is sent to Bloomberg as "<SYMBOL> US Equity".

OUTPUT
    data/prices/prices.parquet  (long, tidy):  date, symbol, px_last
    'symbol' is the plain ticker/ETF (e.g. AAPL, XBI) so the overlay notebooks
    join straight onto your mentions / conviction / signals tables.

WHY blpapi (official) AND NOT xbbg/pdblp
    You said the official SDK is what you have. This uses only blpapi's
    HistoricalDataRequest - no extra wrappers to install. The Terminal must be
    running and logged in; blpapi talks to it on localhost:8194 by default.
"""

import argparse
import datetime
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from update_data import START_DATE, END_DATE, PRICE_TOP_N   # one place to edit
from src.themes import THEME_ETFS                            # theme -> ETF map

PROCESSED = os.path.join(ROOT, "data", "processed")
PRICES_DIR = os.path.join(ROOT, "data", "prices")
OUT_PATH = os.path.join(PRICES_DIR, "prices.parquet")

FIELD = "PX_LAST"
CHUNK = 50            # securities per Bloomberg request (keeps each request small)


# ---------------------------------------------------------------------------
# 1. Decide WHICH symbols to pull - from the same aggregates the notebooks read
# ---------------------------------------------------------------------------
def _read(name):
    path = os.path.join(PROCESSED, name)
    return pd.read_parquet(path) if os.path.exists(path) else None


def window_dates():
    """(start 'YYYYMMDD', end 'YYYYMMDD'). Empty END_DATE means up to today."""
    start = START_DATE.replace("-", "")
    if END_DATE:
        # END_DATE is EXCLUSIVE in the pipeline; Bloomberg endDate is inclusive,
        # so step back one day to keep the same span.
        end_dt = datetime.date.fromisoformat(END_DATE) - datetime.timedelta(days=1)
    else:
        end_dt = datetime.date.today()
    return start, end_dt.strftime("%Y%m%d")


def build_symbol_universe():
    """Return a sorted list of plain symbols (tickers + ETFs) to price."""
    symbols = set()

    # top-N most-mentioned tickers over the window
    counts = _read("daily_ticker_counts.parquet")
    if counts is not None and len(counts):
        c = counts.copy()
        c["date"] = pd.to_datetime(c["date"])
        lo = pd.to_datetime(START_DATE)
        hi = pd.to_datetime(END_DATE) if END_DATE else c["date"].max()
        c = c[(c["date"] >= lo) & (c["date"] <= hi)]
        top = (c.groupby("ticker")["mention_count"].sum()
               .sort_values(ascending=False).head(PRICE_TOP_N).index.tolist())
        symbols.update(top)

    # every theme's ETF
    symbols.update(THEME_ETFS.values())

    # anything named in the signals
    sig_theme = _read("trade_signals.parquet")
    if sig_theme is not None and "etf" in sig_theme.columns:
        symbols.update(sig_theme["etf"].dropna().astype(str))
    sig_tick = _read("trade_signals_tickers.parquet")
    if sig_tick is not None and "ticker" in sig_tick.columns:
        symbols.update(sig_tick["ticker"].dropna().astype(str))

    # clean up: drop blanks, upper-case, sort
    symbols = {s.strip().upper() for s in symbols if s and str(s).strip()}
    return sorted(symbols)


def to_bloomberg(symbol):
    """Plain ticker/ETF -> Bloomberg security string. US-listed equities/ETFs."""
    return f"{symbol} US Equity"


# ---------------------------------------------------------------------------
# 2. Pull the prices (this is the only part that needs the Terminal)
# ---------------------------------------------------------------------------
def pull_prices(symbols, start_yyyymmdd, end_yyyymmdd):
    """Return a long DataFrame: date, symbol, px_last. Uses blpapi directly."""
    import blpapi

    session = blpapi.Session()          # default host localhost, port 8194
    if not session.start():
        raise RuntimeError("could not start blpapi Session - is the Terminal running?")
    try:
        if not session.openService("//blp/refdata"):
            raise RuntimeError("could not open //blp/refdata service")
        refdata = session.getService("//blp/refdata")

        rows = []
        # send the securities in small chunks so each request stays light
        for i in range(0, len(symbols), CHUNK):
            chunk = symbols[i:i + CHUNK]
            request = refdata.createRequest("HistoricalDataRequest")
            for sym in chunk:
                request.getElement("securities").appendValue(to_bloomberg(sym))
            request.getElement("fields").appendValue(FIELD)
            request.set("periodicitySelection", "DAILY")
            request.set("startDate", start_yyyymmdd)
            request.set("endDate", end_yyyymmdd)
            print(f"  requesting {len(chunk)} securities "
                  f"({i + 1}-{i + len(chunk)} of {len(symbols)}) ...", flush=True)
            session.sendRequest(request)

            # drain events until this request's RESPONSE arrives
            done = False
            while not done:
                event = session.nextEvent(500)
                for msg in event:
                    rows.extend(_parse_message(msg))
                if event.eventType() == blpapi.Event.RESPONSE:
                    done = True
        return pd.DataFrame(rows, columns=["date", "symbol", "px_last"])
    finally:
        session.stop()


def _parse_message(msg):
    """Pull (date, symbol, px_last) rows out of one HistoricalData message."""
    out = []
    if not msg.hasElement("securityData"):
        return out
    sec_data = msg.getElement("securityData")
    # Bloomberg gives back 'IBM US Equity'; strip the suffix to the plain symbol.
    security = sec_data.getElementAsString("security")
    symbol = security.replace(" US Equity", "").strip()

    if sec_data.hasElement("securityError"):
        print(f"    (no data for {security})")
        return out

    field_data = sec_data.getElement("fieldData")
    for i in range(field_data.numValues()):
        point = field_data.getValueAsElement(i)
        if not point.hasElement("date") or not point.hasElement(FIELD):
            continue
        d = point.getElementAsDatetime("date")
        px = point.getElementAsFloat(FIELD)
        out.append({"date": f"{d.year:04d}-{d.month:02d}-{d.day:02d}",
                    "symbol": symbol, "px_last": px})
    return out


# ---------------------------------------------------------------------------
# 3. main
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Pull daily close prices from Bloomberg.")
    p.add_argument("--dry-run", action="store_true",
                   help="show the symbol universe + request window; do NOT connect")
    args = p.parse_args()

    symbols = build_symbol_universe()
    start, end = window_dates()
    print("=" * 64)
    print("BLOOMBERG PRICE PULL")
    print(f"  window : {start} -> {end}  (from update_data.py)")
    print(f"  field  : {FIELD} (daily close)")
    print(f"  symbols: {len(symbols)}  (top {PRICE_TOP_N} mentioned + theme ETFs + signals)")
    print(f"           {', '.join(symbols[:25])}{' ...' if len(symbols) > 25 else ''}")
    print("=" * 64)

    if args.dry_run:
        print("--dry-run: nothing pulled, nothing written.")
        return 0
    if not symbols:
        print("no symbols to pull - run update_data.py first so the aggregates exist.")
        return 1

    prices = pull_prices(symbols, start, end)
    if prices.empty:
        print("Bloomberg returned no rows - check the Terminal is logged in.")
        return 1

    os.makedirs(PRICES_DIR, exist_ok=True)
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices.sort_values(["symbol", "date"]).reset_index(drop=True)
    prices.to_parquet(OUT_PATH, index=False)
    print(f"saved {len(prices):,} rows for {prices['symbol'].nunique()} symbols "
          f"-> {OUT_PATH}")
    print("next: open the overlay notebooks (11-14) to compare against your data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

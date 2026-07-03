"""
inflection.py
=============
Find the moment a ticker (or theme) "takes off" in Reddit mentions.

THE IDEA (this is the part to understand):

  - The daily mention count is the raw signal. It is bumpy day to day.
  - The "first derivative" just means: how much did it change since yesterday?
        first_derivative[today] = smoothed_count[today] - smoothed_count[yesterday]
    Think of the count as distance and the first derivative as speed.
    When speed jumps from ~0 to large positive, attention is accelerating -
    that is the inflection / take-off we want to catch early.
  - Because raw counts are noisy, we SMOOTH them first with a rolling average
    (average of the last few days). Otherwise every little wiggle looks like a
    derivative spike.
  - We then call a day an "inflection day" if its first derivative is unusually
    large compared to that ticker's normal day-to-day change. "Unusually large"
    = above mean + (k * standard deviation) of the derivative. That is just a
    simple, standard way of saying "much bigger than typical noise".

INPUT: a daily-counts table with columns: date, ticker, mention_count
       (this is exactly what extract_tickers.py --daily-out produces).

Run example:
  python3 inflection.py \\
      --in outputs/wsb_mentions/wsb_daily_ticker_counts_2021-01.parquet \\
      --ticker GME --smooth 3 --k 2.0
"""

import argparse
import datetime

import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_daily_counts(path):
    """Read the daily counts file (.parquet or .csv) into a DataFrame."""
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    # Make sure 'date' is a real date, not just text, so sorting/plotting works.
    df["date"] = pd.to_datetime(df["date"])
    return df


def build_daily_series(df, ticker, value_col="mention_count"):
    """
    For one ticker, return a table with EVERY calendar day in the range,
    filling days with no mentions as 0.

    value_col : which column to use as the signal -
                "mention_count"  = raw number of mentions
                "weighted_count" = mentions weighted by upvotes
    (If the requested column is missing, we fall back to mention_count.)

    Why fill the gaps? If a ticker is missing on a quiet day, the derivative
    would skip over it and look wrong. A continuous daily line is correct.
    """
    if value_col not in df.columns:
        value_col = "mention_count"

    one = df[df["ticker"] == ticker].copy()
    if one.empty:
        return None
    one = one.sort_values("date")

    # A complete list of days from the first to the last date in the data.
    full_range = pd.date_range(one["date"].min(), one["date"].max(), freq="D")

    # Reindex onto that full range; missing days become 0.
    series = (
        one.set_index("date")[value_col]
        .reindex(full_range)
        .fillna(0)
    )
    series.index.name = "date"
    return series


def compute_inflection(series, smooth_window, k):
    """
    Take a daily count series and return a DataFrame with:
      count           - raw mentions
      smoothed        - rolling average (less noisy)
      velocity        - first derivative (change in smoothed vs yesterday)
      is_inflection   - True on days where velocity is unusually high

    smooth_window : how many days to average over (e.g. 3 or 7)
    k             : how many standard deviations above normal counts as a spike
    """
    out = pd.DataFrame({"count": series})

    # 1) Smooth: average of the last `smooth_window` days.
    out["smoothed"] = out["count"].rolling(window=smooth_window, min_periods=1).mean()

    # 2) First derivative: today's smoothed value minus yesterday's.
    out["velocity"] = out["smoothed"].diff().fillna(0)

    # 3) Threshold = typical change + k * how spread out the changes are.
    average_change = out["velocity"].mean()
    spread = out["velocity"].std()
    threshold = average_change + k * spread

    # 4) Flag the inflection days (growing fast AND clearly above the threshold).
    out["is_inflection"] = (out["velocity"] > threshold) & (out["velocity"] > 0)

    # Keep the threshold around so we can draw it on the plot later.
    out.attrs["threshold"] = threshold
    return out


def plot_inflection(result, ticker, out_path):
    """Two stacked charts: mentions on top, velocity (first derivative) below."""
    dates = result.index
    threshold = result.attrs["threshold"]
    inflection_days = result[result["is_inflection"]]

    fig, (ax_top, ax_bottom) = plt.subplots(
        2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )

    # --- Top: raw + smoothed mentions, with inflection days marked ---
    ax_top.plot(dates, result["count"], color="lightgray", label="raw mentions")
    ax_top.plot(dates, result["smoothed"], color="steelblue", linewidth=2,
                label="smoothed mentions")
    ax_top.scatter(inflection_days.index, inflection_days["smoothed"],
                   color="red", zorder=5, label="inflection day")
    ax_top.set_title(ticker + " - mentions per day")
    ax_top.set_ylabel("mentions")
    ax_top.legend()
    ax_top.grid(True, alpha=0.3)

    # --- Bottom: the first derivative (velocity) and the threshold line ---
    ax_bottom.plot(dates, result["velocity"], color="darkorange",
                   label="first derivative (velocity)")
    ax_bottom.axhline(threshold, color="red", linestyle="--",
                      label="spike threshold")
    ax_bottom.axhline(0, color="black", linewidth=0.6)
    ax_bottom.scatter(inflection_days.index, inflection_days["velocity"],
                      color="red", zorder=5)
    ax_bottom.set_title("First derivative - how fast mentions are growing")
    ax_bottom.set_ylabel("change vs prior day")
    ax_bottom.set_xlabel("date")
    ax_bottom.legend()
    ax_bottom.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()
    print("Saved plot:", out_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Detect mention take-off (inflection) points")
    parser.add_argument("--in", dest="inp", required=True, help="daily counts .parquet or .csv")
    parser.add_argument("--ticker", required=True, help="ticker to analyse, e.g. GME")
    parser.add_argument("--smooth", type=int, default=3, help="rolling average window in days")
    parser.add_argument("--k", type=float, default=2.0, help="std-devs above normal = a spike")
    parser.add_argument("--plot-out", default=None, help="PNG path (default: <ticker>_inflection.png)")
    parser.add_argument("--csv-out", default=None, help="optional CSV of flagged inflection days")
    args = parser.parse_args(argv)

    df = load_daily_counts(args.inp)
    series = build_daily_series(df, args.ticker)
    if series is None:
        print("Ticker", args.ticker, "not found in the data.")
        return 1

    result = compute_inflection(series, args.smooth, args.k)

    plot_path = args.plot_out or (args.ticker + "_inflection.png")
    plot_inflection(result, args.ticker, plot_path)

    # Print the flagged days so you can see them in the terminal.
    flagged = result[result["is_inflection"]]
    print("\nInflection days for", args.ticker, "(", len(flagged), "found ):")
    for date, row in flagged.iterrows():
        print("  ", date.strftime("%Y-%m-%d"),
              "| mentions:", int(row["count"]),
              "| velocity:", round(row["velocity"], 1))

    if args.csv_out:
        flagged.to_csv(args.csv_out)
        print("Saved flagged days to", args.csv_out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

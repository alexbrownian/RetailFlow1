# Which notebooks run where

A notebook runs on the **HP** only if everything it reads is one of the
five text-free aggregates in `data/processed/` (put there by `hydrate()`).
Notebooks that read the raw text (`posts.parquet` / `posts_slice.parquet`) can
only run on the **producer** machine.

| Notebook | Reads | HP? |
|---|---|---|
| `01_clean_data` | `posts.parquet` (raw text) | ❌ producer only |
| `02_mentions_over_time` | `posts_slice.parquet` (raw text) | ❌ producer only |
| `03_first_derivative` | `daily_ticker_counts` | ✅ yes |
| `04_theme_mentions` | `posts_slice.parquet` (raw text) | ❌ producer only |
| `05_theme_first_derivative` | `daily_theme_counts` | ✅ yes |
| `06_ticker_sentiment` | `posts_slice.parquet` (raw text) | ❌ producer only |
| `07_theme_sentiment` | `posts_slice.parquet` (raw text) | ❌ producer only |
| `08_ticker_conviction` | `daily_ticker_sentiment` | ✅ yes |
| `09_theme_conviction` | `daily_theme_sentiment` | ✅ yes |
| `10_trading_signals` | counts + both sentiments | ✅ yes |
| `11_overlay_ticker_mentions` | counts + `data/prices` | ✅ yes |
| `12_overlay_ticker_first_derivative` | counts + `data/prices` | ✅ yes |
| `13_overlay_theme_conviction` | theme conviction + `data/prices` | ✅ yes |
| `14_overlay_trading_signals` | signals + `data/prices` | ✅ yes |

**Runnable on the HP:** 03, 05, 08, 09, 10, 11, 12, 13, 14.
**Producer only (need the raw text):** 01, 02, 04, 06, 07.

`update_data.py` in HP mode automatically runs only 08 → 09 → 10 (the
ones needed to refresh the signals). Run 03/05 and 11–14 by hand when you want
them.

"""
themes.py
=========
Two complementary approaches to theme-level analysis:

APPROACH 1 - Keyword extraction (primary, used by notebooks 04/05)
  Scan each post's raw text for a curated list of keywords/phrases. A post
  about "HBM memory", "DRAM pricing", and "Micron" rolls up into the
  `memory` theme even if it doesn't mention a recognisable ticker symbol.

  Entry point: build_daily_theme_counts(posts_df)
  Returns: DataFrame(date, theme, mention_count, weighted_count)

APPROACH 2 - Ticker grouping (secondary, CLI tool)
  Group the already-extracted ticker counts into themes by mapping each
  ticker to one or more theme buckets. Useful for consistency with the
  ticker-level notebooks.

  Entry point: map_to_themes(df, THEME_TICKERS)
  CLI: python -m src.themes --in daily_ticker_counts.parquet --out daily_theme_counts.parquet
"""

import argparse
import re

import pandas as pd


# ---------------------------------------------------------------------------
# APPROACH 1 — keyword-based theme extraction
# ---------------------------------------------------------------------------
# Each theme maps to a list of keyword phrases. Matching is case-insensitive
# and whole-word (won't match "gold" inside "golden"). Multi-word phrases
# ("short squeeze") are matched as substrings of the lowercased text.
# A ticker can be a keyword too (e.g. "NVDA" in the ai_megacap theme).
# ---------------------------------------------------------------------------
THEME_KEYWORDS: dict[str, list[str]] = {
    # ------------------------------------------------------------------
    # Semiconductors & chips
    # ------------------------------------------------------------------
    "semiconductors": [
        "semiconductor", "semis", "chipmaker", "chips", "chip",
        "fab", "wafer", "foundry", "lithography", "EUV", "ASML",
        "TSMC", "TSM", "Intel", "INTC", "Broadcom", "AVGO",
        "Qualcomm", "QCOM", "Texas Instruments", "TXN", "Marvell",
        "MRVL", "ON Semi", "ON Semiconductor", "Microchip", "MCHP",
        "silicon", "node", "process node", "3nm", "5nm", "7nm",
    ],
    # ------------------------------------------------------------------
    # Memory / DRAM / NAND
    # ------------------------------------------------------------------
    "memory": [
        "memory", "DRAM", "HBM", "HBM2", "HBM3", "HBM4",
        "NAND", "flash", "flash storage", "DDR4", "DDR5",
        "Micron", "MU", "Samsung", "SK Hynix", "Hynix",
        "bandwidth memory", "high bandwidth", "memory chip",
        "storage chip", "solid state", "SSD",
    ],
    # ------------------------------------------------------------------
    # AI / machine learning
    # ------------------------------------------------------------------
    "ai": [
        "AI", "artificial intelligence", "machine learning", "deep learning",
        "LLM", "large language model", "GPT", "ChatGPT", "generative AI",
        "neural network", "inference", "AI training", "AI chip",
        "data center AI", "Nvidia AI", "CUDA", "transformer",
        "foundation model", "AGI", "OpenAI", "Anthropic",
    ],
    # ------------------------------------------------------------------
    # Nvidia & AI megacaps
    # ------------------------------------------------------------------
    "ai_megacap": [
        "NVDA", "Nvidia", "MSFT", "Microsoft", "GOOGL", "Google", "Alphabet",
        "META", "Meta", "Facebook", "AAPL", "Apple", "AMZN", "Amazon",
        "TSLA", "Tesla", "mag7", "magnificent seven", "big tech",
        "hyperscaler", "hyperscalers",
    ],
    # ------------------------------------------------------------------
    # Crypto / digital assets
    # ------------------------------------------------------------------
    "crypto": [
        "bitcoin", "BTC", "ethereum", "ETH", "crypto", "cryptocurrency",
        "defi", "DeFi", "blockchain", "altcoin", "NFT", "web3",
        "Coinbase", "COIN", "MicroStrategy", "MSTR", "MARA", "Marathon",
        "RIOT", "Riot", "GBTC", "BITO", "HUT", "stablecoin",
        "halving", "mining rig", "hash rate",
    ],
    # ------------------------------------------------------------------
    # Gold & precious metals / commodities
    # ------------------------------------------------------------------
    "gold_metals": [
        "gold", "GLD", "silver", "SLV", "precious metal", "metals",
        "Newmont", "NEM", "Barrick", "GOLD", "Agnico", "AEM",
        "Freeport", "FCX", "copper", "platinum", "palladium",
        "commodity", "commodities", "inflation hedge",
    ],
    # ------------------------------------------------------------------
    # Oil & energy
    # ------------------------------------------------------------------
    "energy": [
        "oil", "crude", "WTI", "Brent", "natural gas", "LNG",
        "energy stock", "oil stock", "XOM", "Exxon", "CVX", "Chevron",
        "BP", "Shell", "COP", "ConocoPhillips", "refinery", "pipeline",
        "OPEC", "oilfield", "shale", "fracking",
    ],
    # ------------------------------------------------------------------
    # Renewables / clean energy / EV
    # ------------------------------------------------------------------
    "ev_clean_energy": [
        "electric vehicle", "EV", "Tesla", "TSLA", "Rivian", "RIVN",
        "Lucid", "LCID", "NIO", "Xpeng", "XPEV", "Li Auto", "LI",
        "battery", "lithium", "lithium ion", "charging station",
        "solar", "wind energy", "nuclear", "renewable", "clean energy",
        "ENPH", "Enphase", "FSLR", "First Solar",
    ],
    # ------------------------------------------------------------------
    # Short squeeze / gamma squeeze
    # ------------------------------------------------------------------
    "short_squeeze": [
        "short squeeze", "gamma squeeze", "squeeze", "short interest",
        "days to cover", "float", "low float", "heavily shorted",
        "short seller", "short position", "naked short", "MOASS",
        "mother of all short squeezes", "cover shorts", "covering",
        "put options", "borrow rate",
    ],
    # ------------------------------------------------------------------
    # Meme stocks / retail frenzy
    # ------------------------------------------------------------------
    "meme_stocks": [
        "meme stock", "meme", "GME", "GameStop", "AMC",
        "BB", "BlackBerry", "BBBY", "Bed Bath", "CLOV", "SNDL",
        "KOSS", "reddit rally", "WSB", "wallstreetbets",
        "retail investor", "apes", "yolo", "diamond hands",
        "paper hands", "tendies", "moon", "to the moon",
    ],
    # ------------------------------------------------------------------
    # Biotech & pharma
    # ------------------------------------------------------------------
    "biotech_pharma": [
        "biotech", "pharma", "pharmaceutical", "FDA", "FDA approval",
        "clinical trial", "phase 1", "phase 2", "phase 3",
        "drug approval", "cancer drug", "oncology", "MRNA",
        "Moderna", "Pfizer", "PFE", "Merck", "MRK",
        "AstraZeneca", "AZN", "Eli Lilly", "LLY",
        "weight loss drug", "GLP-1", "ozempic", "semaglutide",
        "gene therapy", "CRISPR", "antibody",
    ],
    # ------------------------------------------------------------------
    # Macro / rates / Fed
    # ------------------------------------------------------------------
    "macro_rates": [
        "interest rate", "federal reserve", "Fed", "FOMC",
        "rate hike", "rate cut", "inflation", "CPI", "PPI",
        "recession", "soft landing", "hard landing",
        "yield curve", "bond yield", "treasury", "10-year",
        "GDP", "unemployment", "jobs report", "payroll",
        "macro", "stagflation", "tightening", "pivot",
    ],
    # ------------------------------------------------------------------
    # Real estate / REITs
    # ------------------------------------------------------------------
    "real_estate": [
        "real estate", "REIT", "housing market", "home price",
        "mortgage rate", "30-year mortgage", "refinancing",
        "commercial real estate", "office space", "multifamily",
        "SPG", "Simon Property", "O", "Realty Income",
        "VNQ", "landlord", "rent", "eviction",
    ],
    # ------------------------------------------------------------------
    # Cloud / SaaS / software
    # ------------------------------------------------------------------
    "cloud_saas": [
        "cloud", "SaaS", "software as a service", "AWS", "Azure",
        "Google Cloud", "GCP", "cloud computing", "subscription revenue",
        "ARR", "annual recurring revenue", "churn",
        "Salesforce", "CRM", "Snowflake", "SNOW", "Palantir", "PLTR",
        "Datadog", "DDOG", "MongoDB", "MDB", "Cloudflare", "NET",
    ],
    # ------------------------------------------------------------------
    # Options / derivatives / volatility
    # ------------------------------------------------------------------
    "options_volatility": [
        "options", "call option", "put option", "LEAPS",
        "implied volatility", "IV crush", "theta", "gamma",
        "delta", "vega", "0DTE", "zero DTE",
        "VIX", "volatility", "earnings play", "strangle", "straddle",
        "iron condor", "covered call", "cash secured put",
    ],
    # ------------------------------------------------------------------
    # China / geopolitics / trade
    # ------------------------------------------------------------------
    "china_geopolitics": [
        "China", "Chinese", "tariff", "trade war", "sanctions",
        "Taiwan", "geopolitical", "decoupling", "supply chain",
        "export control", "BABA", "Alibaba", "JD", "JD.com",
        "PDD", "Tencent", "Baidu", "BIDU", "Huawei",
    ],
    # ------------------------------------------------------------------
    # Banks & financials
    # ------------------------------------------------------------------
    "financials": [
        "bank", "banking", "JPMorgan", "JPM", "Goldman Sachs", "GS",
        "Morgan Stanley", "MS", "Bank of America", "BAC",
        "Wells Fargo", "WFC", "Citigroup", "C",
        "credit card", "regional bank", "SVB", "Silicon Valley Bank",
        "credit default swap", "CDS", "earnings beat",
    ],
    # ------------------------------------------------------------------
    # Consumer / retail
    # ------------------------------------------------------------------
    "consumer_retail": [
        "consumer", "retail", "spending", "Walmart", "WMT",
        "Amazon", "AMZN", "Target", "TGT", "Costco", "COST",
        "consumer sentiment", "discretionary", "e-commerce",
        "holiday sales", "Black Friday", "back to school",
    ],
    # ------------------------------------------------------------------
    # Earnings / results
    # ------------------------------------------------------------------
    "earnings": [
        "earnings", "earnings call", "EPS", "earnings per share",
        "beat expectations", "miss expectations", "guidance",
        "revenue beat", "revenue miss", "forward guidance",
        "outlook", "Q1", "Q2", "Q3", "Q4", "quarterly results",
        "earnings season",
    ],
    # ------------------------------------------------------------------
    # IPO / SPACs / new listings
    # ------------------------------------------------------------------
    "ipo_spac": [
        "IPO", "initial public offering", "SPAC", "blank check",
        "de-SPAC", "direct listing", "lockup expiry", "lock-up",
        "pre-IPO", "going public", "listing",
    ],
}

# Pre-compile a regex for each theme so matching is fast.
_THEME_PATTERNS: dict[str, re.Pattern] = {}


def _get_patterns() -> dict[str, re.Pattern]:
    if _THEME_PATTERNS:
        return _THEME_PATTERNS
    for theme, keywords in THEME_KEYWORDS.items():
        # Sort longest keywords first so multi-word phrases beat single words.
        sorted_kw = sorted(keywords, key=len, reverse=True)
        # Escape each keyword and wrap in a word-boundary (or space boundary for phrases).
        parts = []
        for kw in sorted_kw:
            escaped = re.escape(kw)
            if " " in kw:
                parts.append(escaped)          # phrase: no word boundary needed
            else:
                parts.append(r"\b" + escaped + r"\b")
        _THEME_PATTERNS[theme] = re.compile("|".join(parts), re.IGNORECASE)
    return _THEME_PATTERNS


def build_daily_theme_counts(posts_df: pd.DataFrame) -> pd.DataFrame:
    """
    Scan each post's title + selftext for theme keywords.

    posts_df must have columns: date, title, selftext, score.

    Returns DataFrame(date, theme, mention_count, weighted_count)
    where mention_count is how many times ANY keyword for that theme
    appeared in the post, and weighted_count weights those hits by upvotes.
    """
    patterns = _get_patterns()
    have_score = "score" in posts_df.columns

    titles  = posts_df["title"].fillna("").astype(str)
    bodies  = posts_df["selftext"].fillna("").astype(str)
    dates   = posts_df["date"].astype(str)
    scores  = posts_df["score"].fillna(0).astype(int) if have_score else [0] * len(posts_df)

    rows = []
    for date, title, body, score in zip(dates, titles, bodies, scores):
        text = title + " " + body
        for theme, pat in patterns.items():
            hits = pat.findall(text)
            if hits:
                rows.append({
                    "date": date,
                    "theme": theme,
                    "hit_count": len(hits),
                    "score": score,
                })

    if not rows:
        return pd.DataFrame(columns=["date", "theme", "mention_count", "weighted_count"])

    long_df = pd.DataFrame(rows)
    daily = (
        long_df.groupby(["date", "theme"])
        .agg(
            mention_count=("hit_count", "sum"),
            weighted_count=("score", "sum"),
        )
        .reset_index()
    )
    return daily


# ---------------------------------------------------------------------------
# APPROACH 2 — ticker grouping (secondary)
# ---------------------------------------------------------------------------
THEME_TICKERS: dict[str, set[str]] = {
    "semiconductors": {"NVDA", "AMD", "SMH", "TSM", "INTC", "MU", "AVGO", "SMCI", "ASML"},
    "crypto":         {"COIN", "MSTR", "MARA", "RIOT", "BITO", "GBTC", "HUT"},
    "gold_metals":    {"GLD", "SLV", "GOLD", "NEM", "AEM", "FCX"},
    "ai_megacap":     {"NVDA", "MSFT", "GOOGL", "META", "AAPL", "AMZN", "TSLA"},
    "meme_stocks":    {"GME", "AMC", "BB", "BBBY", "CLOV", "SNDL", "KOSS"},
}


def build_ticker_to_themes(theme_tickers):
    lookup = {}
    for theme, tickers in theme_tickers.items():
        for ticker in tickers:
            lookup.setdefault(ticker, []).append(theme)
    return lookup


def map_to_themes(df, theme_tickers):
    """Turn a (date, ticker, mention_count) table into (date, ticker=THEME, mention_count)."""
    lookup = build_ticker_to_themes(theme_tickers)

    rows = []
    for date, ticker, count in zip(df["date"], df["ticker"], df["mention_count"]):
        themes = lookup.get(ticker)
        if not themes:
            continue
        for theme in themes:
            rows.append({"date": date, "ticker": theme, "mention_count": count})

    theme_df = pd.DataFrame(rows)
    if theme_df.empty:
        return theme_df

    grouped = (
        theme_df.groupby(["date", "ticker"], as_index=False)["mention_count"]
        .sum()
    )
    return grouped


# ---------------------------------------------------------------------------
# CLI (approach 2 only — approach 1 is called from notebooks directly)
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="Roll ticker mentions up into themes")
    parser.add_argument("--in", dest="inp", required=True, help="daily ticker counts .parquet/.csv")
    parser.add_argument("--out", required=True, help="output daily theme counts path")
    args = parser.parse_args(argv)

    df = pd.read_parquet(args.inp) if args.inp.endswith(".parquet") else pd.read_csv(args.inp)
    theme_df = map_to_themes(df, THEME_TICKERS)

    if theme_df.empty:
        print("No tickers matched any theme - check THEME_TICKERS.")
        return 1

    if args.out.endswith(".parquet"):
        theme_df.to_parquet(args.out, index=False)
    else:
        theme_df.to_csv(args.out, index=False)

    totals = theme_df.groupby("ticker")["mention_count"].sum().sort_values(ascending=False)
    print("Saved", len(theme_df), "rows to", args.out)
    print("\nTotal mentions per theme:")
    for theme, total in totals.items():
        print("  ", theme, ":", int(total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

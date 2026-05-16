"""
Master data collection runner.
Run manually or via scheduler (e.g., Windows Task Scheduler / cron).

Usage:
  python -m data_collector.collect_all              # full collection
  python -m data_collector.collect_all --fast       # equity-only, no sentiment
  python -m data_collector.collect_all --options    # F&O chains only
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from config.instruments import NIFTY_200, FNO_UNIVERSE, Segment
from config.settings import settings

from data_collector.market_data      import collect_daily
from data_collector.fundamental      import collect_fundamentals
from data_collector.option_chain     import collect_option_chain, collect_market_pcr
from data_collector.penny_stocks     import collect_penny_candidates
from data_collector.earnings_calendar import collect_earnings_calendar, collect_historical_results
from data_collector.twitter_sentiment import collect_sentiment
from data_collector.news_collector   import collect_news
from data_collector.fii_dii          import collect_fii_dii

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("collect_all")

# Build symbol → sector map from Nifty 200
SYMBOL_SECTOR: dict[str, str] = {i.symbol: i.sector for i in NIFTY_200}
SYMBOL_COMPANY: dict[str, str] = {i.symbol: i.name for i in NIFTY_200}


def _parallel(fn, items, max_workers: int = 8, delay: float = 0.3):
    """Run fn on each item in parallel, respecting rate limits with a small delay."""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for item in items:
            f = pool.submit(fn, item)
            futures[f] = item
            time.sleep(delay)  # stagger to avoid API rate limits
        for future in as_completed(futures):
            sym = futures[future]
            try:
                results[sym] = future.result()
            except Exception as exc:
                logger.error("Failed %s: %s", sym, exc)
    return results


def collect_equity(symbols: list[str]) -> None:
    logger.info("── Equity OHLCV + Indicators ──────────────────")
    _parallel(collect_daily, symbols, max_workers=10)


def collect_fundamentals_all(symbols: list[str]) -> None:
    logger.info("── Fundamentals ────────────────────────────────")

    def _collect(sym):
        return collect_fundamentals(sym, SYMBOL_SECTOR.get(sym, "Unknown"))

    _parallel(_collect, symbols, max_workers=5, delay=0.5)


def collect_options(fo_symbols: list[str]) -> None:
    logger.info("── Option Chains ───────────────────────────────")
    collect_market_pcr()
    _parallel(
        lambda s: collect_option_chain(s, is_index=("NIFTY" in s or "BANKNIFTY" in s)),
        fo_symbols,
        max_workers=4,
        delay=1.0,  # NSE rate-limits hard — be polite
    )


def collect_news_all(symbols: list[str]) -> None:
    logger.info("── News (Finnhub) ───────────────────────────────")
    _parallel(collect_news, symbols, max_workers=5, delay=0.5)


def collect_sentiment_all(symbols: list[str]) -> None:
    logger.info("── Twitter Sentiment ────────────────────────────")

    def _collect(sym):
        return collect_sentiment(sym, SYMBOL_COMPANY.get(sym, ""))

    _parallel(_collect, symbols, max_workers=3, delay=1.0)


def collect_earnings_all(symbols: list[str]) -> None:
    logger.info("── Earnings Calendar ────────────────────────────")
    collect_earnings_calendar(symbols)
    _parallel(collect_historical_results, symbols, max_workers=5, delay=0.5)


def full_collection(fast: bool = False) -> None:
    start = datetime.now()
    logger.info("=== Starting full data collection [fast=%s] ===", fast)

    # Tier 1: Nifty 200 equity symbols
    equity_syms = [i.symbol for i in NIFTY_200]

    # Tier 2: F&O universe
    fo_syms = FNO_UNIVERSE[:30]  # limit to top 30 to avoid NSE rate limits

    # FII/DII (single call)
    try:
        collect_fii_dii()
    except Exception as exc:
        logger.error("FII/DII failed: %s", exc)

    # Equity OHLCV
    collect_equity(equity_syms)

    # Fundamentals (daily only)
    collect_fundamentals_all(equity_syms)

    # Options
    collect_options(fo_syms)

    # Penny scanner
    try:
        collect_penny_candidates()
    except Exception as exc:
        logger.error("Penny scan failed: %s", exc)

    # Earnings
    collect_earnings_all(equity_syms[:50])  # top 50 for calendar

    if not fast:
        # News (API-rate-limited — do fewer symbols if needed)
        collect_news_all(equity_syms[:50])

        # Twitter (rate-limited — only for top 20 high-priority symbols)
        priority_symbols = [
            "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFOSYS", "SBIN",
            "HINDUNILVR", "ITC", "LT", "BAJFINANCE",
            "ADANIENT", "TATAMOTORS", "MARUTI", "SUNPHARMA", "WIPRO",
            "AXISBANK", "KOTAKBANK", "NTPC", "POWERGRID", "BEL",
        ]
        collect_sentiment_all(priority_symbols)

    elapsed = (datetime.now() - start).total_seconds()
    logger.info("=== Collection complete in %.1f seconds ===", elapsed)


def main() -> None:
    parser = argparse.ArgumentParser(description="India Auto-Trader data collector")
    parser.add_argument("--fast",    action="store_true", help="Skip Twitter + extended news")
    parser.add_argument("--options", action="store_true", help="Options chains only")
    parser.add_argument("--penny",   action="store_true", help="Penny scanner only")
    parser.add_argument("--equity",  action="store_true", help="Equity OHLCV only")
    args = parser.parse_args()

    if args.options:
        collect_options(FNO_UNIVERSE[:30])
    elif args.penny:
        collect_penny_candidates()
    elif args.equity:
        collect_equity([i.symbol for i in NIFTY_200])
    else:
        full_collection(fast=args.fast)


if __name__ == "__main__":
    main()

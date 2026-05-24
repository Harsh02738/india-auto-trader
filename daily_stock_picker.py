"""
Daily Stock Picker — runs at 10:00 AM IST to select intraday trading candidates.

Process:
  1. Use yfinance to get Nifty 500 top movers by intraday volume/price change.
  2. Call Claude API with web_search tool to validate picks with today's news/market context.
  3. Filter by price range, volume, and liquidity.
  4. Write final list to data/daily_stocks_{date}.json.

The trade engine reads this file to know which stocks to scan during the session.
Falls back to top-10 Nifty 50 by volume if anything fails.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

import yfinance as yf

from config.settings import settings

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Fallback universe if picker fails
_FALLBACK_STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
    "SBIN", "AXISBANK", "KOTAKBANK", "LT", "MARUTI",
]

# Nifty 500 high-beta candidates for scanning (diverse sectors)
_SCAN_UNIVERSE = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK",
    "KOTAKBANK", "LT", "MARUTI", "TATAMOTORS", "TATASTEEL", "HINDALCO",
    "JSWSTEEL", "BAJFINANCE", "BAJAJFINSV", "WIPRO", "HCLTECH", "TECHM",
    "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP",
    "ADANIPORTS", "ADANIENT", "POWERGRID", "NTPC", "ONGC", "BPCL",
    "COALINDIA", "INDUSINDBK", "FEDERALBNK", "BANDHANBNK", "IDFCFIRSTB",
    "M&M", "HEROMOTOCO", "BAJAJ-AUTO", "EICHERMOT", "TVSMOTOR",
    "VEDL", "SAIL", "NMDC", "NATIONALUM", "HINDCOPPER",
    "ZOMATO", "NYKAA", "PAYTM", "IRCTC", "DMART",
    "TITAN", "JUBLFOOD", "NESTLEIND", "BRITANNIA", "HINDUNILVR",
    "GRASIM", "ULTRACEMCO", "ACC", "AMBUJACEMENT", "SHREECEM",
    "ITC", "GODREJCP", "MARICO", "COLPAL", "EMAMILTD",
    "LUPIN", "TORNTPHARM", "AUROPHARMA", "BIOCON", "GLAND",
    "DELHIVERY", "INDIAMART", "JUSTDIAL", "NAUKRI", "POLICYBZR",
]


def _pick_by_volume_change(symbols: list[str], max_picks: int) -> list[str]:
    """
    Use yfinance intraday data to rank symbols by today's volume ratio and
    absolute price change. Returns top candidates.
    """
    scores: list[tuple[float, str]] = []
    min_price = settings.stock_picker_min_price
    max_price = settings.stock_picker_max_price
    min_vol   = settings.stock_picker_min_volume

    for sym in symbols:
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="2d", interval="1d")
            if hist.empty or len(hist) < 2:
                continue

            today_vol  = int(hist["Volume"].iloc[-1])
            prev_vol   = int(hist["Volume"].iloc[-2])
            today_close = float(hist["Close"].iloc[-1])
            prev_close  = float(hist["Close"].iloc[-2])

            if today_close < min_price or today_close > max_price:
                continue
            if today_vol < min_vol:
                continue

            vol_ratio   = today_vol / max(prev_vol, 1)
            pct_change  = abs((today_close - prev_close) / max(prev_close, 1))
            score = vol_ratio * 0.6 + pct_change * 100 * 0.4   # weight volume more
            scores.append((score, sym))

        except Exception as exc:
            logger.debug("yfinance error for %s: %s", sym, exc)

    scores.sort(reverse=True)
    return [sym for _, sym in scores[:max_picks * 2]]   # double; Claude will trim


def _ask_claude(candidates: list[str]) -> list[str]:
    """
    Call Claude API with web_search to validate/refine candidates using today's news.
    Returns a final ordered list of NSE symbols.
    """
    if not settings.anthropic_api_key:
        logger.info("[StockPicker] No ANTHROPIC_API_KEY — skipping Claude validation")
        return candidates[: settings.stock_picker_max_stocks]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        today = date.today().strftime("%d %B %Y")
        prompt = (
            f"Today is {today}. I am selecting NSE Indian equity stocks for intraday trading today.\n\n"
            f"My technical screen identified these candidates (sorted by volume/momentum):\n"
            f"{', '.join(candidates)}\n\n"
            f"Please search the web for today's market news and conditions, then:\n"
            f"1. Identify which of these stocks have strong intraday momentum catalysts today "
            f"(earnings, news, sector tailwinds, FII activity, analyst upgrades)\n"
            f"2. Remove any stocks with negative news (regulatory issues, earnings miss, sector headwinds)\n"
            f"3. Add up to 2 additional high-momentum stocks NOT in my list if there are compelling "
            f"news-driven opportunities today on NSE\n"
            f"4. Return ONLY a Python list of 5-8 NSE ticker symbols (e.g. [\"RELIANCE\", \"TATAMOTORS\"])\n\n"
            f"Rules: equity only (no F&O index), price ₹{settings.stock_picker_min_price:.0f}-"
            f"₹{settings.stock_picker_max_price:.0f}, avg daily volume > 5L shares.\n"
            f"Output format: just the Python list, nothing else."
        )

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text from response
        full_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                full_text += block.text

        # Parse the list from Claude's response
        match = re.search(r'\[([^\]]+)\]', full_text)
        if match:
            raw = match.group(1)
            symbols = [s.strip().strip('"').strip("'").upper() for s in raw.split(",")]
            symbols = [s for s in symbols if re.match(r'^[A-Z&\-]+$', s) and len(s) <= 15]
            if symbols:
                logger.info("[StockPicker] Claude selected: %s", symbols)
                return symbols[: settings.stock_picker_max_stocks]

    except Exception as exc:
        logger.warning("[StockPicker] Claude API call failed: %s", exc)

    return candidates[: settings.stock_picker_max_stocks]


def pick_stocks_for_today(force: bool = False) -> list[str]:
    """
    Main entry point. Returns list of NSE symbols to trade today.
    Writes result to data/daily_stocks_{date}.json.

    Args:
        force: If True, re-run even if today's file already exists.
    """
    today = str(date.today())
    out_path = DATA_DIR / f"daily_stocks_{today}.json"

    if not force and out_path.exists():
        try:
            data = json.loads(out_path.read_text())
            symbols = data.get("symbols", [])
            if symbols:
                logger.info("[StockPicker] Loaded existing picks for %s: %s", today, symbols)
                return symbols
        except Exception:
            pass

    logger.info("[StockPicker] Running daily stock selection for %s", today)

    # Step 1: Technical screen via yfinance
    candidates = _pick_by_volume_change(_SCAN_UNIVERSE, settings.stock_picker_max_stocks * 2)
    if not candidates:
        logger.warning("[StockPicker] Technical screen returned no candidates, using fallback")
        candidates = _FALLBACK_STOCKS

    logger.info("[StockPicker] Technical candidates: %s", candidates)

    # Step 2: Claude validation + web search
    final_symbols = _ask_claude(candidates)

    if not final_symbols:
        final_symbols = candidates[: settings.stock_picker_max_stocks]

    # Step 3: Persist
    payload = {
        "date": today,
        "symbols": final_symbols,
        "candidates_screened": candidates,
        "picked_at": datetime.now(tz=timezone.utc).isoformat(),
        "method": "yfinance_volume_rank + claude_web_search",
    }
    out_path.write_text(json.dumps(payload, indent=2))
    logger.info("[StockPicker] Final picks for %s: %s → %s", today, len(final_symbols), final_symbols)
    return final_symbols


def load_today_stocks() -> list[str]:
    """Load today's stock picks from file. Returns fallback if file missing."""
    today = str(date.today())
    path = DATA_DIR / f"daily_stocks_{today}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return data.get("symbols", _FALLBACK_STOCKS)
        except Exception:
            pass
    return _FALLBACK_STOCKS


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    picks = pick_stocks_for_today(force=True)
    print(f"\nToday's picks ({len(picks)} stocks): {picks}")

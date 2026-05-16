"""
Fetches fundamental data via yfinance and computes scoring against sector benchmarks.
Writes data/fundamentals/{SYMBOL}_fund.json once daily.
"""

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

from config.instruments import SECTOR_PE_BENCHMARKS

logger = logging.getLogger(__name__)

DATA_DIR = Path("data/fundamentals")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _safe(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def _score_pe(pe: float | None, sector: str) -> float:
    """0-1 score: 1=cheap, 0=very expensive."""
    if pe is None or pe <= 0:
        return 0.5
    low, high = SECTOR_PE_BENCHMARKS.get(sector, (15.0, 35.0))
    if pe <= low:
        return 1.0
    if pe >= high * 1.5:
        return 0.0
    if pe <= high:
        return 1.0 - (pe - low) / (high - low) * 0.5
    return max(0.0, 0.5 - (pe - high) / high)


def _score_roe(roe: float | None) -> float:
    """ROE > 20% = excellent. < 8% = poor."""
    if roe is None:
        return 0.5
    if roe >= 0.25:
        return 1.0
    if roe >= 0.15:
        return 0.8
    if roe >= 0.10:
        return 0.6
    if roe >= 0.05:
        return 0.4
    return 0.2


def _score_de(de: float | None) -> float:
    """D/E < 0.5 = great. > 3 = risky."""
    if de is None:
        return 0.5
    if de <= 0:
        return 1.0
    if de <= 0.5:
        return 0.9
    if de <= 1.0:
        return 0.75
    if de <= 2.0:
        return 0.55
    if de <= 3.0:
        return 0.35
    return 0.1


def _score_revenue_growth(growth: float | None) -> float:
    """YoY revenue growth. > 20% = great, < 0% = bad."""
    if growth is None:
        return 0.5
    if growth >= 0.25:
        return 1.0
    if growth >= 0.15:
        return 0.8
    if growth >= 0.08:
        return 0.65
    if growth >= 0.0:
        return 0.5
    if growth >= -0.10:
        return 0.3
    return 0.1


def _score_profit_margin(margin: float | None) -> float:
    """Net profit margin. Varies by sector but this is a generic scorer."""
    if margin is None:
        return 0.5
    if margin >= 0.20:
        return 1.0
    if margin >= 0.12:
        return 0.8
    if margin >= 0.06:
        return 0.6
    if margin >= 0.0:
        return 0.4
    return 0.1


def collect_fundamentals(symbol: str, sector: str = "Unknown") -> dict:
    ticker_symbol = f"{symbol}.NS"
    logger.info("Fetching fundamentals for %s", ticker_symbol)

    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info or {}
    except Exception as exc:
        logger.error("yfinance error for %s: %s", symbol, exc)
        return {}

    # ── Extract key metrics ────────────────────────────────────────────────────
    pe          = _safe(info.get("trailingPE"))
    fwd_pe      = _safe(info.get("forwardPE"))
    eps_ttm     = _safe(info.get("trailingEps"))
    eps_next_yr = _safe(info.get("forwardEps"))
    roe         = _safe(info.get("returnOnEquity"))   # as decimal (0.15 = 15%)
    roa         = _safe(info.get("returnOnAssets"))
    de_ratio    = _safe(info.get("debtToEquity"))     # yfinance gives D/E × 100 sometimes
    profit_margin = _safe(info.get("profitMargins"))
    op_margin   = _safe(info.get("operatingMargins"))
    revenue     = _safe(info.get("totalRevenue"))
    rev_growth  = _safe(info.get("revenueGrowth"))    # YoY
    earn_growth = _safe(info.get("earningsGrowth"))
    market_cap  = _safe(info.get("marketCap"))
    book_value  = _safe(info.get("bookValue"))
    pb_ratio    = _safe(info.get("priceToBook"))
    current_price = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))
    beta        = _safe(info.get("beta"))

    # Fix D/E: yfinance sometimes returns it multiplied by 100
    if de_ratio is not None and de_ratio > 30:
        de_ratio = de_ratio / 100.0

    # ── EPS growth from quarterly income statement ─────────────────────────────
    eps_growth_yoy: float | None = None
    try:
        qf = ticker.quarterly_financials
        if not qf.empty and "Net Income" in qf.index:
            net_inc = qf.loc["Net Income"]
            if len(net_inc) >= 5:
                recent_4q = net_inc.iloc[:4].sum()
                prior_4q  = net_inc.iloc[4:8].sum() if len(net_inc) >= 8 else None
                if prior_4q and prior_4q != 0:
                    eps_growth_yoy = round(float((recent_4q - prior_4q) / abs(prior_4q)), 4)
    except Exception:
        pass

    # ── Revenue trend (last 4 quarters) ──────────────────────────────────────
    quarterly_revenue: list[dict] = []
    try:
        qf = ticker.quarterly_financials
        if not qf.empty and "Total Revenue" in qf.index:
            rev_row = qf.loc["Total Revenue"].head(4)
            for date_idx, val in rev_row.items():
                quarterly_revenue.append({
                    "quarter": str(date_idx)[:10],
                    "revenue": int(val) if not math.isnan(float(val)) else None,
                })
    except Exception:
        pass

    # ── Scoring ───────────────────────────────────────────────────────────────
    score_pe     = _score_pe(pe, sector)
    score_roe    = _score_roe(roe)
    score_de     = _score_de(de_ratio)
    score_growth = _score_revenue_growth(rev_growth)
    score_margin = _score_profit_margin(profit_margin)

    fundamental_score = round(
        score_pe * 0.25 +
        score_roe * 0.25 +
        score_de * 0.20 +
        score_growth * 0.15 +
        score_margin * 0.15,
        4
    )

    # ── Flags ─────────────────────────────────────────────────────────────────
    flags: list[str] = []
    if de_ratio is not None and de_ratio > 2.0:
        flags.append("HIGH_LEVERAGE")
    if roe is not None and roe < 0.10:
        flags.append("LOW_ROE")
    if pe is not None and pe > 0:
        _, high_pe = SECTOR_PE_BENCHMARKS.get(sector, (15.0, 35.0))
        if pe > high_pe * 1.5:
            flags.append("EXPENSIVE_PE")
    if rev_growth is not None and rev_growth < 0:
        flags.append("NEGATIVE_REV_GROWTH")
    if profit_margin is not None and profit_margin < 0:
        flags.append("LOSS_MAKING")

    payload = {
        "symbol":           symbol,
        "sector":           sector,
        "timestamp":        datetime.now(tz=timezone.utc).isoformat(),

        # Valuation
        "pe_ratio":         pe,
        "forward_pe":       fwd_pe,
        "pb_ratio":         pb_ratio,
        "market_cap":       market_cap,
        "current_price":    current_price,

        # Profitability
        "eps_ttm":          eps_ttm,
        "eps_next_year":    eps_next_yr,
        "eps_growth_yoy":   eps_growth_yoy,
        "roe":              roe,
        "roa":              roa,
        "profit_margin":    profit_margin,
        "operating_margin": op_margin,

        # Leverage & growth
        "de_ratio":         de_ratio,
        "revenue":          revenue,
        "revenue_growth":   rev_growth,
        "earnings_growth":  earn_growth,
        "book_value":       book_value,
        "beta":             beta,

        # Quarterly breakdown
        "quarterly_revenue": quarterly_revenue,

        # Scores (0–1)
        "score_pe":          round(score_pe, 4),
        "score_roe":         round(score_roe, 4),
        "score_de":          round(score_de, 4),
        "score_growth":      round(score_growth, 4),
        "score_margin":      round(score_margin, 4),
        "fundamental_score": fundamental_score,

        # Flags
        "flags": flags,
    }

    out_path = DATA_DIR / f"{symbol}_fund.json"
    out_path.write_text(json.dumps(payload, indent=2))
    logger.info("Wrote %s (score=%.3f)", out_path, fundamental_score)
    return payload


def run(symbol_sector_map: dict[str, str]) -> dict[str, dict]:
    results = {}
    for sym, sector in symbol_sector_map.items():
        try:
            results[sym] = collect_fundamentals(sym, sector)
        except Exception as exc:
            logger.error("Failed %s: %s", sym, exc)
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    test_map = {"RELIANCE": "Energy", "TCS": "IT", "HDFCBANK": "Banking"}
    if len(sys.argv) > 1:
        test_map = {s: "Unknown" for s in sys.argv[1:]}
    run(test_map)

"""
Scans NSE SME platform and applies all penny-stock filters.
Writes data/penny/candidates.json with ranked, screened candidates.
"""

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from config.settings import settings

logger = logging.getLogger(__name__)

PENNY_DIR = Path("data/penny")
PENNY_DIR.mkdir(parents=True, exist_ok=True)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

# SEBI watchlist / suspended / caution scrips endpoint
NSE_CAUTION_URL = "https://www.nseindia.com/api/caution-list"
NSE_SME_EMERGE_URL = "https://www.nseindia.com/api/live-analysis-emerge"

# Bulk deals show insider/promoter buying
NSE_BULK_DEALS_URL = "https://www.nseindia.com/api/bulk-deals"


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _fetch_nse_json(url: str) -> dict | list:
    with httpx.Client(headers=NSE_HEADERS, follow_redirects=True, timeout=30) as client:
        client.get("https://www.nseindia.com")
        time.sleep(0.3)
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def _fetch_caution_symbols() -> set[str]:
    """Returns NSE caution/suspended symbols to auto-reject."""
    try:
        data = _fetch_nse_json(NSE_CAUTION_URL)
        if isinstance(data, dict) and "data" in data:
            return {r.get("symbol", "").upper() for r in data["data"]}
        return set()
    except Exception as exc:
        logger.warning("Could not fetch caution list: %s", exc)
        return set()


def _fetch_recent_promoter_buys(symbol: str) -> bool:
    """True if promoter/insider bulk deal buy in last 30 days."""
    try:
        data = _fetch_nse_json(NSE_BULK_DEALS_URL)
        if isinstance(data, dict) and "data" in data:
            for deal in data["data"]:
                if (deal.get("symbol", "").upper() == symbol.upper()
                        and "BUY" in str(deal.get("buySell", "")).upper()
                        and "PROMOTER" in str(deal.get("clientName", "")).upper()):
                    return True
    except Exception:
        pass
    return False


def _get_sme_candidates() -> list[str]:
    """Get the NSE SME EMERGE listed symbols."""
    try:
        data = _fetch_nse_json(NSE_SME_EMERGE_URL)
        if isinstance(data, dict) and "data" in data:
            return [r.get("symbol", "") for r in data["data"] if r.get("symbol")]
    except Exception as exc:
        logger.warning("Could not fetch SME list: %s", exc)

    # Fallback: return a hardcoded seed list of active SME stocks
    return [
        "IDEAFORGE", "KPIGREEN", "WAAREEENER", "GREENPANEL", "SAKSOFT",
        "RPGLIFE", "PARAS", "BAJAJHIND", "INVENTURE", "DRONAYUGA",
        "POCL", "GHCL", "PENTAGOLD", "AIMCO", "RUPA",
        "GANECOS", "TGBHOTELS", "COCHINSHIP", "GRSE",
    ]


def _score_penny_stock(info: dict, price: float, avg_volume: int, symbol: str) -> dict:
    """
    Apply all filters and compute a penny score.
    Returns a dict with 'passed': bool, 'score': float, 'flags': list[str], 'reject_reason': str | None
    """
    flags: list[str] = []
    reject_reason: str | None = None

    market_cap_cr = _safe_float(info.get("marketCap"))
    if market_cap_cr:
        market_cap_cr /= 1e7  # Convert to crores

    promoter_pct  = _safe_float(info.get("heldPercentInsiders"))     # 0–1
    de_ratio      = _safe_float(info.get("debtToEquity"))             # sometimes × 100
    rev_ttm       = _safe_float(info.get("totalRevenue"))
    profit_margin = _safe_float(info.get("profitMargins"))

    # Fix D/E
    if de_ratio is not None and de_ratio > 30:
        de_ratio /= 100.0

    # ── Hard filters (any failure = REJECT) ───────────────────────────────────
    if market_cap_cr is None:
        reject_reason = "NO_MARKET_CAP_DATA"
    elif market_cap_cr < settings.penny_min_market_cap_cr:
        reject_reason = f"MARKET_CAP_TOO_SMALL_{market_cap_cr:.0f}Cr"
    elif market_cap_cr > settings.penny_max_market_cap_cr:
        reject_reason = f"MARKET_CAP_TOO_LARGE_{market_cap_cr:.0f}Cr"
    elif price < settings.penny_min_price or price > settings.penny_max_price:
        reject_reason = f"PRICE_OUT_OF_RANGE_{price}"
    elif avg_volume < settings.penny_min_avg_volume:
        reject_reason = f"LOW_VOLUME_{avg_volume}"
    elif promoter_pct is not None and promoter_pct < settings.penny_min_promoter_holding_pct:
        reject_reason = f"LOW_PROMOTER_HOLDING_{promoter_pct*100:.1f}%"
    elif de_ratio is not None and de_ratio > settings.penny_max_de_ratio:
        reject_reason = f"HIGH_DE_{de_ratio:.2f}"
    elif profit_margin is not None and profit_margin < -0.20:
        reject_reason = "DEEP_LOSSES"

    # Promoter pledging check (yfinance doesn't expose this directly; flag as unknown)
    # In production, cross-check with BSE shareholding pattern
    pledging_unknown = True

    if reject_reason:
        return {
            "passed":         False,
            "score":          0.0,
            "flags":          flags,
            "reject_reason":  reject_reason,
            "market_cap_cr":  round(market_cap_cr, 1) if market_cap_cr else None,
            "promoter_pct":   round(promoter_pct * 100, 1) if promoter_pct else None,
            "de_ratio":       de_ratio,
            "pledging":       "UNKNOWN" if pledging_unknown else None,
        }

    # ── Warning flags ─────────────────────────────────────────────────────────
    if pledging_unknown:
        flags.append("PLEDGING_UNVERIFIED")
    if profit_margin is not None and profit_margin < 0:
        flags.append("LOSS_MAKING")
    if de_ratio is not None and de_ratio > 1.5:
        flags.append("ELEVATED_LEVERAGE")

    # ── Scoring (0–1) ─────────────────────────────────────────────────────────
    score_mcap = 0.5  # neutral — within range
    score_vol  = min(avg_volume / 200_000, 1.0)   # 200K+ = max score
    score_prom = min((promoter_pct or 0.30) / 0.60, 1.0)  # 60%+ = max
    score_de   = max(0.0, 1.0 - (de_ratio or 0.5) / 2.0)
    score_prof = max(0.0, min((profit_margin or 0.0) + 0.20, 0.40) / 0.40)

    score = (
        score_vol  * 0.30 +
        score_prom * 0.25 +
        score_de   * 0.20 +
        score_prof * 0.15 +
        score_mcap * 0.10
    )

    return {
        "passed":         True,
        "score":          round(score, 4),
        "flags":          flags,
        "reject_reason":  None,
        "market_cap_cr":  round(market_cap_cr, 1) if market_cap_cr else None,
        "promoter_pct":   round(promoter_pct * 100, 1) if promoter_pct else None,
        "de_ratio":       de_ratio,
        "profit_margin":  profit_margin,
        "pledging":       "UNKNOWN",
    }


def _check_operator_activity(symbol: str, avg_volume: int, current_volume: int, price_5d_chg: float) -> dict:
    """Flag potential operator/pump-and-dump patterns."""
    flags: list[str] = []

    if avg_volume > 0:
        vol_spike = current_volume / avg_volume
        if vol_spike > settings.penny_volume_spike_threshold:
            flags.append(f"VOLUME_SPIKE_{vol_spike:.1f}x")

    if price_5d_chg > 0.50:
        flags.append(f"PARABOLIC_MOVE_{price_5d_chg*100:.0f}%_5D")

    caution = bool(flags)
    return {"caution": caution, "operator_flags": flags}


def collect_penny_candidates() -> dict:
    caution_symbols = _fetch_caution_symbols()
    sme_symbols = _get_sme_candidates()

    candidates: list[dict] = []
    rejected: list[dict] = []

    for symbol in sme_symbols:
        if symbol.upper() in caution_symbols:
            logger.info("Skipping %s — in NSE caution list", symbol)
            continue

        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            info = ticker.info or {}
            hist = ticker.history(period="1mo", interval="1d")

            if hist.empty:
                logger.debug("No price history for %s", symbol)
                continue

            price = float(hist["Close"].iloc[-1])
            avg_volume = int(hist["Volume"].tail(20).mean()) if len(hist) >= 5 else 0
            current_volume = int(hist["Volume"].iloc[-1])

            # 5-day price change
            price_5d_ago = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else price
            price_5d_chg = (price - price_5d_ago) / price_5d_ago if price_5d_ago else 0.0

            screen = _score_penny_stock(info, price, avg_volume, symbol)
            operator = _check_operator_activity(symbol, avg_volume, current_volume, price_5d_chg)

            if screen["passed"] and not operator["caution"]:
                # Check for promoter buying catalyst
                promoter_buying = _fetch_recent_promoter_buys(symbol)

                entry = {
                    "symbol":           symbol,
                    "price":            round(price, 2),
                    "avg_volume_20d":   avg_volume,
                    "price_5d_chg_pct": round(price_5d_chg * 100, 2),
                    "market_cap_cr":    screen["market_cap_cr"],
                    "promoter_pct":     screen["promoter_pct"],
                    "de_ratio":         screen["de_ratio"],
                    "pledging":         screen["pledging"],
                    "profit_margin":    screen.get("profit_margin"),
                    "score":            screen["score"],
                    "flags":            screen["flags"],
                    "promoter_buying":  promoter_buying,
                    "stop_loss":        round(price * (1 - settings.penny_stop_loss_pct), 2),
                    "target_low":       round(price * (1 + settings.penny_target_pct_low), 2),
                    "target_high":      round(price * (1 + settings.penny_target_pct_high), 2),
                }
                candidates.append(entry)
            elif operator["caution"]:
                logger.info("CAUTION %s: operator activity %s", symbol, operator["operator_flags"])
                rejected.append({
                    "symbol":        symbol,
                    "reject_reason": "OPERATOR_ACTIVITY",
                    "flags":         operator["operator_flags"],
                })
            else:
                rejected.append({
                    "symbol":        symbol,
                    "reject_reason": screen["reject_reason"],
                    "flags":         screen["flags"],
                })

        except Exception as exc:
            logger.error("Error scanning %s: %s", symbol, exc)

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)

    payload = {
        "timestamp":       datetime.now(tz=timezone.utc).isoformat(),
        "scanned_count":   len(sme_symbols),
        "passed_count":    len(candidates),
        "rejected_count":  len(rejected),
        "candidates":      candidates[:20],  # top 20
        "rejected_sample": rejected[:10],
    }

    (PENNY_DIR / "candidates.json").write_text(json.dumps(payload, indent=2))
    logger.info("Penny scan: %d/%d passed filters", len(candidates), len(sme_symbols))
    return payload


def run() -> dict:
    return collect_penny_candidates()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run()

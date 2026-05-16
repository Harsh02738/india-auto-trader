"""
Fetches NSE option chain data, computes PCR, max pain, OI buildup/unwinding.
Writes:
  data/options/{SYMBOL}_chain.json   — full chain with IV, OI, LTP per strike
  data/options/{SYMBOL}_oi.json      — OI analysis: support/resistance strikes
  data/options/market_pcr.json       — Nifty + BankNifty PCR snapshot
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

OPTIONS_DIR = Path("data/options")
OPTIONS_DIR.mkdir(parents=True, exist_ok=True)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

NSE_BASE = "https://www.nseindia.com"
CHAIN_URL = NSE_BASE + "/api/option-chain-indices?symbol={symbol}"
EQUITY_CHAIN_URL = NSE_BASE + "/api/option-chain-equities?symbol={symbol}"


def _nse_session() -> httpx.Client:
    """Create an httpx client that has an NSE cookie (required for their API)."""
    client = httpx.Client(headers=NSE_HEADERS, follow_redirects=True, timeout=30)
    # Touch the main page first to get cookies
    try:
        client.get(NSE_BASE)
        time.sleep(0.5)
    except Exception:
        pass
    return client


def _fetch_chain_raw(symbol: str, is_index: bool = False) -> dict:
    url = CHAIN_URL.format(symbol=symbol) if is_index else EQUITY_CHAIN_URL.format(symbol=symbol)
    with _nse_session() as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def _compute_max_pain(strikes: list[float], call_oi: dict[float, int], put_oi: dict[float, int]) -> float:
    """
    Max pain = strike at which total ITM option value (loss to option buyers) is maximum.
    i.e., the strike at which writers profit the most — price gravitates toward it near expiry.
    """
    min_pain = float("inf")
    max_pain_strike = strikes[len(strikes) // 2] if strikes else 0.0

    for test_strike in strikes:
        # Loss to call holders: sum of (test_strike - strike) * OI for all ITM calls
        call_loss = sum(
            max(0, test_strike - s) * oi for s, oi in call_oi.items()
        )
        # Loss to put holders: sum of (strike - test_strike) * OI for all ITM puts
        put_loss = sum(
            max(0, s - test_strike) * oi for s, oi in put_oi.items()
        )
        total_loss = call_loss + put_loss
        if total_loss < min_pain:
            min_pain = total_loss
            max_pain_strike = test_strike

    return max_pain_strike


def _pcr_signal(pcr: float) -> str:
    if pcr >= 1.50:
        return "EXTREME_BEARISH_SENTIMENT_CONTRARIAN_BUY"
    if pcr >= 1.30:
        return "BEARISH_SENTIMENT_MILD_BUY_BIAS"
    if pcr >= 0.80:
        return "NEUTRAL"
    if pcr >= 0.50:
        return "BULLISH_SENTIMENT_MILD_SELL_BIAS"
    return "EXTREME_BULLISH_SENTIMENT_CONTRARIAN_SELL"


def collect_option_chain(symbol: str, is_index: bool = False) -> dict:
    logger.info("Fetching option chain for %s (index=%s)", symbol, is_index)

    try:
        raw = _fetch_chain_raw(symbol, is_index)
    except Exception as exc:
        logger.error("NSE chain fetch failed for %s: %s", symbol, exc)
        return {}

    records = raw.get("records", {})
    data_rows = records.get("data", [])
    expiry_dates: list[str] = records.get("expiryDates", [])
    underlying_value: float = records.get("underlyingValue", 0.0)
    straddle_price: float = 0.0

    if not data_rows:
        logger.warning("Empty chain data for %s", symbol)
        return {}

    # ── Parse strikes ──────────────────────────────────────────────────────────
    call_oi: dict[float, int] = {}
    put_oi: dict[float, int] = {}
    chain_rows: list[dict] = []

    for row in data_rows:
        strike = float(row.get("strikePrice", 0))
        expiry = row.get("expiryDate", "")

        ce = row.get("CE", {})
        pe = row.get("PE", {})

        c_oi   = int(ce.get("openInterest", 0))
        c_chg  = int(ce.get("changeinOpenInterest", 0))
        c_vol  = int(ce.get("totalTradedVolume", 0))
        c_iv   = float(ce.get("impliedVolatility", 0.0))
        c_ltp  = float(ce.get("lastPrice", 0.0))

        p_oi   = int(pe.get("openInterest", 0))
        p_chg  = int(pe.get("changeinOpenInterest", 0))
        p_vol  = int(pe.get("totalTradedVolume", 0))
        p_iv   = float(pe.get("impliedVolatility", 0.0))
        p_ltp  = float(pe.get("lastPrice", 0.0))

        call_oi[strike] = c_oi
        put_oi[strike] = p_oi

        # PCR per strike
        strike_pcr = round(p_oi / c_oi, 3) if c_oi > 0 else None

        chain_rows.append({
            "strike":     strike,
            "expiry":     expiry,
            "ce_oi":      c_oi,
            "ce_oi_chg":  c_chg,
            "ce_vol":     c_vol,
            "ce_iv":      round(c_iv, 2),
            "ce_ltp":     round(c_ltp, 2),
            "pe_oi":      p_oi,
            "pe_oi_chg":  p_chg,
            "pe_vol":     p_vol,
            "pe_iv":      round(p_iv, 2),
            "pe_ltp":     round(p_ltp, 2),
            "pcr":        strike_pcr,
        })

        # Straddle at ATM
        if abs(strike - underlying_value) < 50 or (straddle_price == 0 and chain_rows):
            straddle_price = c_ltp + p_ltp

    # ── Aggregate OI metrics ───────────────────────────────────────────────────
    total_call_oi = sum(call_oi.values())
    total_put_oi  = sum(put_oi.values())
    pcr_overall   = round(total_put_oi / total_call_oi, 4) if total_call_oi > 0 else 0.0

    strikes_sorted = sorted(call_oi.keys())
    max_pain_strike = _compute_max_pain(strikes_sorted, call_oi, put_oi)

    # Top OI strikes = support/resistance levels
    top_call_oi_strikes = sorted(call_oi, key=call_oi.get, reverse=True)[:5]  # resistance
    top_put_oi_strikes  = sorted(put_oi, key=put_oi.get, reverse=True)[:5]    # support

    # ── ATM strike ────────────────────────────────────────────────────────────
    atm_strike = min(strikes_sorted, key=lambda s: abs(s - underlying_value)) if strikes_sorted else 0.0

    # ── OI buildup classification ─────────────────────────────────────────────
    # We can only classify if we have prior day data; for now we emit raw change OI
    total_ce_oi_chg = sum(int(r["ce_oi_chg"]) for r in chain_rows)
    total_pe_oi_chg = sum(int(r["pe_oi_chg"]) for r in chain_rows)

    chain_payload = {
        "symbol":             symbol,
        "timestamp":          datetime.now(tz=timezone.utc).isoformat(),
        "underlying_value":   round(underlying_value, 2),
        "atm_strike":         atm_strike,
        "expiry_dates":       expiry_dates,
        "straddle_price":     round(straddle_price, 2),

        # PCR
        "pcr":                pcr_overall,
        "pcr_signal":         _pcr_signal(pcr_overall),
        "total_call_oi":      total_call_oi,
        "total_put_oi":       total_put_oi,

        # Max pain
        "max_pain_strike":    max_pain_strike,
        "max_pain_diff_pct":  round((max_pain_strike - underlying_value) / underlying_value * 100, 2) if underlying_value else 0.0,

        # OI levels
        "resistance_strikes": top_call_oi_strikes,   # call writers at these levels
        "support_strikes":    top_put_oi_strikes,     # put writers defend these

        # Chain rows
        "chain": chain_rows,
    }

    oi_payload = {
        "symbol":           symbol,
        "timestamp":        datetime.now(tz=timezone.utc).isoformat(),
        "underlying_value": round(underlying_value, 2),
        "atm_strike":       atm_strike,
        "pcr":              pcr_overall,
        "total_ce_oi_chg":  total_ce_oi_chg,
        "total_pe_oi_chg":  total_pe_oi_chg,
        "resistance_strikes": [
            {"strike": s, "call_oi": call_oi[s]} for s in top_call_oi_strikes
        ],
        "support_strikes": [
            {"strike": s, "put_oi": put_oi[s]} for s in top_put_oi_strikes
        ],
        "max_pain_strike": max_pain_strike,
    }

    (OPTIONS_DIR / f"{symbol}_chain.json").write_text(json.dumps(chain_payload, indent=2))
    (OPTIONS_DIR / f"{symbol}_oi.json").write_text(json.dumps(oi_payload, indent=2))
    logger.info("Wrote chain + OI for %s | PCR=%.2f | MaxPain=%s", symbol, pcr_overall, max_pain_strike)

    return chain_payload


def collect_market_pcr() -> dict:
    """Collect PCR snapshot for Nifty and BankNifty."""
    results: dict[str, dict] = {}
    for sym in ["NIFTY", "BANKNIFTY"]:
        try:
            data = collect_option_chain(sym, is_index=True)
            if data:
                results[sym] = {
                    "pcr":             data["pcr"],
                    "pcr_signal":      data["pcr_signal"],
                    "max_pain_strike": data["max_pain_strike"],
                    "underlying":      data["underlying_value"],
                }
        except Exception as exc:
            logger.error("PCR collection failed for %s: %s", sym, exc)

    payload = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "indices":   results,
        "market_sentiment": (
            "BEARISH" if any(v["pcr"] >= 1.3 for v in results.values()) else
            "BULLISH" if any(v["pcr"] <= 0.7 for v in results.values()) else
            "NEUTRAL"
        ),
    }
    (OPTIONS_DIR / "market_pcr.json").write_text(json.dumps(payload, indent=2))
    return payload


def run(symbols: list[str], index_symbols: list[str] | None = None) -> None:
    if index_symbols is None:
        index_symbols = ["NIFTY", "BANKNIFTY"]
    for sym in index_symbols:
        try:
            collect_option_chain(sym, is_index=True)
        except Exception as exc:
            logger.error("Index chain failed %s: %s", sym, exc)
    for sym in symbols:
        try:
            collect_option_chain(sym, is_index=False)
        except Exception as exc:
            logger.error("Equity chain failed %s: %s", sym, exc)
    collect_market_pcr()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    syms = sys.argv[1:] or ["RELIANCE"]
    run(syms)

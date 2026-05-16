"""
Fetches FII/DII (Foreign & Domestic Institutional Investor) daily flow data from NSE.
Writes data/sentiment/fii_dii.json.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

SENTIMENT_DIR = Path("data/sentiment")
SENTIMENT_DIR.mkdir(parents=True, exist_ok=True)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"


def _nse_get(url: str) -> dict | list:
    with httpx.Client(headers=NSE_HEADERS, follow_redirects=True, timeout=30) as client:
        client.get("https://www.nseindia.com")
        time.sleep(0.3)
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def _parse_crores(val) -> float | None:
    """Parse a value that may be a string like '2,345.67' or a number."""
    if val is None:
        return None
    try:
        if isinstance(val, str):
            val = val.replace(",", "").replace("(", "-").replace(")", "")
        return round(float(val), 2)
    except (ValueError, TypeError):
        return None


def _signal(fii_net: float | None, dii_net: float | None) -> str:
    """Interpret FII + DII combined flow."""
    if fii_net is None or dii_net is None:
        return "UNKNOWN"

    fii_bull = fii_net > 2000
    fii_bear = fii_net < -2000
    dii_bull = dii_net > 500
    dii_bear = dii_net < -500

    if fii_bull and dii_bull:
        return "VERY_BULLISH"
    if fii_bull:
        return "BULLISH"
    if fii_bear and dii_bear:
        return "VERY_BEARISH"
    if fii_bear and dii_bull:
        return "STABILIZING"   # DII buying offsets FII selling
    if fii_bear:
        return "BEARISH"
    return "NEUTRAL"


def collect_fii_dii() -> dict:
    logger.info("Fetching FII/DII data from NSE")

    try:
        raw = _nse_get(FII_DII_URL)
    except Exception as exc:
        logger.error("FII/DII fetch failed: %s", exc)
        return {}

    rows = raw if isinstance(raw, list) else raw.get("data", [])
    if not rows:
        logger.warning("Empty FII/DII response")
        return {}

    daily_records: list[dict] = []

    for row in rows[:30]:  # last 30 trading days
        date_str = str(row.get("date") or row.get("tradeDate", ""))

        # FII columns (may vary across NSE API versions)
        fii_buy  = _parse_crores(row.get("fiiBuyValue")  or row.get("FII_BUY"))
        fii_sell = _parse_crores(row.get("fiiSellValue") or row.get("FII_SELL"))
        fii_net  = _parse_crores(row.get("fiiNetValue")  or row.get("FII_NET"))
        if fii_net is None and fii_buy is not None and fii_sell is not None:
            fii_net = fii_buy - fii_sell

        # DII columns
        dii_buy  = _parse_crores(row.get("diiBuyValue")  or row.get("DII_BUY"))
        dii_sell = _parse_crores(row.get("diiSellValue") or row.get("DII_SELL"))
        dii_net  = _parse_crores(row.get("diiNetValue")  or row.get("DII_NET"))
        if dii_net is None and dii_buy is not None and dii_sell is not None:
            dii_net = dii_buy - dii_sell

        daily_records.append({
            "date":     date_str,
            "fii_buy":  fii_buy,
            "fii_sell": fii_sell,
            "fii_net":  fii_net,
            "dii_buy":  dii_buy,
            "dii_sell": dii_sell,
            "dii_net":  dii_net,
            "signal":   _signal(fii_net, dii_net),
        })

    # Latest day summary
    latest = daily_records[0] if daily_records else {}
    fii_net_latest = latest.get("fii_net")
    dii_net_latest = latest.get("dii_net")

    # 5-day cumulative flows
    fii_5d = sum(r["fii_net"] or 0 for r in daily_records[:5])
    dii_5d = sum(r["dii_net"] or 0 for r in daily_records[:5])

    payload = {
        "timestamp":        datetime.now(tz=timezone.utc).isoformat(),
        "latest_date":      latest.get("date"),
        "fii_net_today":    fii_net_latest,
        "dii_net_today":    dii_net_latest,
        "fii_net_5d":       round(fii_5d, 2),
        "dii_net_5d":       round(dii_5d, 2),
        "signal_today":     _signal(fii_net_latest, dii_net_latest),
        "signal_5d":        _signal(fii_5d / 5, dii_5d / 5) if daily_records else "UNKNOWN",
        "daily":            daily_records,
    }

    (SENTIMENT_DIR / "fii_dii.json").write_text(json.dumps(payload, indent=2))
    logger.info("FII/DII: today FII=%s DII=%s signal=%s",
                fii_net_latest, dii_net_latest, payload["signal_today"])
    return payload


def run() -> dict:
    return collect_fii_dii()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run()

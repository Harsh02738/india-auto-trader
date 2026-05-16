"""
Fetches NSE/BSE earnings calendar and historical quarterly results.
Uses Finnhub for analyst estimates + NSE corporate actions for dates.
Writes:
  data/earnings/calendar.json               — upcoming results (next 30 days)
  data/earnings/{SYMBOL}_results.json       — last 8 quarters + estimates
"""

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import finnhub
import httpx
import yfinance as yf

from config.settings import settings

logger = logging.getLogger(__name__)

EARNINGS_DIR = Path("data/earnings")
EARNINGS_DIR.mkdir(parents=True, exist_ok=True)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

NSE_CORPORATE_ACTIONS = "https://www.nseindia.com/api/corporateActions?index=equities&from_date={from_date}&to_date={to_date}&csv=false"


def _safe(val) -> float | None:
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _finnhub_client() -> finnhub.Client | None:
    if not settings.finnhub_api_key:
        return None
    return finnhub.Client(api_key=settings.finnhub_api_key)


def _fetch_nse_corporate_actions(days_ahead: int = 30) -> list[dict]:
    """Fetch upcoming corporate actions from NSE (includes board meetings / results)."""
    today = datetime.now().strftime("%d-%m-%Y")
    future = (datetime.now() + timedelta(days=days_ahead)).strftime("%d-%m-%Y")
    url = NSE_CORPORATE_ACTIONS.format(from_date=today, to_date=future)

    try:
        with httpx.Client(headers=NSE_HEADERS, follow_redirects=True, timeout=30) as client:
            client.get("https://www.nseindia.com")
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("data", [])
    except Exception as exc:
        logger.warning("NSE corporate actions fetch failed: %s", exc)
        return []


def _classify_earnings_sentiment(revenue_growth: float | None, pat_growth: float | None,
                                  margin_change: float | None) -> str:
    """Classify quarterly result quality."""
    if revenue_growth is None or pat_growth is None:
        return "UNKNOWN"
    if revenue_growth > 0.15 and pat_growth > 0.20:
        return "STRONG_BEAT"
    if revenue_growth > 0.05 and pat_growth > 0.05:
        return "MODERATE_BEAT"
    if revenue_growth >= 0 and pat_growth >= 0:
        return "IN_LINE"
    if revenue_growth < 0 or pat_growth < -0.10:
        return "MISS"
    return "WEAK"


def _count_consecutive_beats(results: list[dict]) -> int:
    """Count how many consecutive quarters the stock beat estimates."""
    count = 0
    for r in results:
        if r.get("vs_estimate") == "BEAT":
            count += 1
        else:
            break
    return count


def collect_earnings_calendar(symbols: list[str]) -> dict:
    """Build upcoming earnings calendar for watchlist."""
    upcoming_actions = _fetch_nse_corporate_actions(days_ahead=30)

    # Filter to board meetings / results announcements
    results_actions = [
        a for a in upcoming_actions
        if any(kw in str(a.get("purpose", "")).upper()
               for kw in ["RESULTS", "QUARTERLY", "FINANCIAL", "BOARD MEETING"])
    ]

    calendar_entries: list[dict] = []
    fh = _finnhub_client()

    for action in results_actions:
        sym = str(action.get("symbol", "")).upper()
        if sym not in [s.upper() for s in symbols]:
            continue

        result_date = action.get("exDate") or action.get("recDate")

        # Try to get analyst EPS estimate from Finnhub
        eps_estimate: float | None = None
        revenue_estimate: float | None = None
        if fh:
            try:
                fh_sym = sym  # NSE symbols work with Finnhub for Indian stocks
                estimates = fh.earnings_calendar(
                    _from=datetime.now().strftime("%Y-%m-%d"),
                    to=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
                    symbol=fh_sym,
                    international=False,
                )
                if estimates.get("earningsCalendar"):
                    for est in estimates["earningsCalendar"]:
                        if est.get("symbol") == sym:
                            eps_estimate = _safe(est.get("epsEstimate"))
                            revenue_estimate = _safe(est.get("revenueEstimate"))
                            break
            except Exception:
                pass

        # Get historical beat/miss record from yfinance
        consecutive_beats = 0
        try:
            ticker = yf.Ticker(f"{sym}.NS")
            hist_earnings = ticker.earnings_history
            if hist_earnings is not None and not hist_earnings.empty:
                records = []
                for idx, row in hist_earnings.iterrows():
                    actual = _safe(row.get("Reported EPS") or row.get("epsActual"))
                    estimate = _safe(row.get("EPS Estimate") or row.get("epsEstimate"))
                    vs_est = "BEAT" if (actual and estimate and actual > estimate) else "MISS"
                    records.append({"vs_estimate": vs_est})
                consecutive_beats = _count_consecutive_beats(records)
        except Exception:
            pass

        # Compute days until results
        try:
            rd = datetime.strptime(result_date, "%d-%b-%Y") if result_date else None
            days_away = (rd - datetime.now()).days if rd else None
        except Exception:
            days_away = None
            rd = None

        calendar_entries.append({
            "symbol":             sym,
            "result_date":        result_date,
            "days_until_results": days_away,
            "purpose":            action.get("purpose"),
            "eps_estimate":       eps_estimate,
            "revenue_estimate":   revenue_estimate,
            "consecutive_beats":  consecutive_beats,
            "setup_rating":       (
                "STRONG" if consecutive_beats >= 3 and days_away and days_away <= 7 else
                "MODERATE" if consecutive_beats >= 2 else
                "WEAK"
            ),
        })

    # Sort by days_until_results
    calendar_entries.sort(key=lambda x: x.get("days_until_results") or 999)

    payload = {
        "timestamp":   datetime.now(tz=timezone.utc).isoformat(),
        "total":       len(calendar_entries),
        "calendar":    calendar_entries,
    }

    (EARNINGS_DIR / "calendar.json").write_text(json.dumps(payload, indent=2))
    logger.info("Earnings calendar: %d upcoming results", len(calendar_entries))
    return payload


def collect_historical_results(symbol: str) -> dict:
    """Collect last 8 quarters of results + Finnhub estimates."""
    logger.info("Fetching historical results for %s", symbol)

    quarterly: list[dict] = []
    fh = _finnhub_client()

    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        qf = ticker.quarterly_financials

        if not qf.empty:
            revenue_row    = qf.loc["Total Revenue"]     if "Total Revenue"    in qf.index else None
            pat_row        = qf.loc["Net Income"]        if "Net Income"       in qf.index else None
            op_income_row  = qf.loc["Operating Income"]  if "Operating Income" in qf.index else None
            ebitda_row     = qf.loc["EBITDA"]            if "EBITDA"           in qf.index else None

            quarters_to_check = min(8, len(qf.columns))
            for i in range(quarters_to_check):
                col = qf.columns[i]
                date_str = str(col)[:10]

                rev     = _safe(revenue_row.iloc[i])   if revenue_row is not None else None
                pat     = _safe(pat_row.iloc[i])        if pat_row is not None else None
                op_inc  = _safe(op_income_row.iloc[i]) if op_income_row is not None else None
                ebitda  = _safe(ebitda_row.iloc[i])    if ebitda_row is not None else None

                # YoY comparison (4 quarters ago)
                rev_yoy = pat_yoy = margin_chg = None
                if i + 4 < quarters_to_check:
                    prev_rev = _safe(revenue_row.iloc[i + 4]) if revenue_row is not None else None
                    prev_pat = _safe(pat_row.iloc[i + 4]) if pat_row is not None else None
                    if rev and prev_rev and prev_rev != 0:
                        rev_yoy = round((rev - prev_rev) / abs(prev_rev), 4)
                    if pat and prev_pat and prev_pat != 0:
                        pat_yoy = round((pat - prev_pat) / abs(prev_pat), 4)

                quarterly.append({
                    "quarter":       date_str,
                    "revenue":       rev,
                    "pat":           pat,
                    "op_income":     op_inc,
                    "ebitda":        ebitda,
                    "rev_growth_yoy": rev_yoy,
                    "pat_growth_yoy": pat_yoy,
                    "result_quality": _classify_earnings_sentiment(rev_yoy, pat_yoy, margin_chg),
                    "vs_estimate":   "UNKNOWN",  # will be filled from Finnhub below
                })

    except Exception as exc:
        logger.error("yfinance quarterly fetch failed for %s: %s", symbol, exc)

    # Enrich with Finnhub actuals vs estimates
    if fh and quarterly:
        try:
            surprises = fh.company_earnings(symbol, limit=8)
            for s in surprises:
                q_date = str(s.get("period", ""))[:7]
                for q in quarterly:
                    if q["quarter"][:7] == q_date:
                        actual = _safe(s.get("actual"))
                        estimate = _safe(s.get("estimate"))
                        if actual is not None and estimate is not None:
                            q["vs_estimate"] = "BEAT" if actual > estimate else "MISS"
                            q["eps_actual"]    = actual
                            q["eps_estimate"]  = estimate
                            q["eps_surprise_pct"] = round((actual - estimate) / abs(estimate) * 100, 2) if estimate else None
        except Exception as exc:
            logger.warning("Finnhub earnings failed for %s: %s", symbol, exc)

    consecutive_beats = _count_consecutive_beats(quarterly)

    payload = {
        "symbol":            symbol,
        "timestamp":         datetime.now(tz=timezone.utc).isoformat(),
        "consecutive_beats": consecutive_beats,
        "quarters":          quarterly,
        "revenue_trend":     (
            "ACCELERATING" if len(quarterly) >= 2 and
            (quarterly[0].get("rev_growth_yoy") or 0) > (quarterly[1].get("rev_growth_yoy") or 0)
            else "DECELERATING"
        ),
    }

    (EARNINGS_DIR / f"{symbol}_results.json").write_text(json.dumps(payload, indent=2))
    logger.info("Wrote historical results for %s (%d quarters)", symbol, len(quarterly))
    return payload


def run(symbols: list[str]) -> None:
    collect_earnings_calendar(symbols)
    for sym in symbols:
        try:
            collect_historical_results(sym)
        except Exception as exc:
            logger.error("Historical results failed %s: %s", sym, exc)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    syms = sys.argv[1:] or ["RELIANCE", "TCS", "HDFCBANK", "INFOSYS"]
    run(syms)

"""
Collects financial news via Finnhub API.
Classifies catalyst type and scores sentiment.
Writes data/news/{SYMBOL}_news.json.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import finnhub
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from config.settings import settings

logger = logging.getLogger(__name__)

NEWS_DIR = Path("data/news")
NEWS_DIR.mkdir(parents=True, exist_ok=True)

_vader = SentimentIntensityAnalyzer()

# Catalyst classification keywords
CATALYST_KEYWORDS: dict[str, list[str]] = {
    "EARNINGS":        ["results", "earnings", "quarterly", "profit", "revenue", "EPS", "PAT"],
    "CONTRACT":        ["contract", "order", "deal", "win", "awarded", "tender"],
    "REGULATORY":      ["SEBI", "regulation", "compliance", "fine", "penalty", "approval", "ban"],
    "FII_FLOW":        ["FII", "FPI", "foreign", "institutional", "inflow", "outflow"],
    "MANAGEMENT":      ["CEO", "MD", "director", "appointment", "resignation", "management"],
    "ACQUISITION":     ["acquisition", "merger", "takeover", "buyout", "stake", "joint venture"],
    "CAPEX":           ["capex", "expansion", "plant", "capacity", "investment", "project"],
    "DIVIDEND":        ["dividend", "buyback", "bonus", "split"],
    "ANALYST":         ["upgrade", "downgrade", "target price", "buy", "sell", "hold", "overweight"],
    "SECTOR_MACRO":    ["RBI", "government", "budget", "GST", "policy", "interest rate", "inflation"],
    "FDA_DRUG":        ["FDA", "USFDA", "drug", "approval", "ANDA", "NDA", "clinical"],
}


def _classify_catalyst(headline: str, summary: str = "") -> str:
    text = (headline + " " + summary).lower()
    for catalyst, keywords in CATALYST_KEYWORDS.items():
        if any(kw.lower() in text for kw in keywords):
            return catalyst
    return "GENERAL"


def _score_news(headline: str, summary: str = "") -> float:
    """Vader compound score on headline + summary."""
    text = headline
    if summary:
        text += ". " + summary[:300]
    return _vader.polarity_scores(text)["compound"]


def _classify(score: float) -> str:
    if score >= 0.05:
        return "POSITIVE"
    if score <= -0.05:
        return "NEGATIVE"
    return "NEUTRAL"


def collect_news(symbol: str, days_back: int = 7) -> dict:
    if not settings.finnhub_api_key:
        logger.warning("FINNHUB_API_KEY not set — skipping news for %s", symbol)
        return {}

    fh = finnhub.Client(api_key=settings.finnhub_api_key)

    today = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    try:
        # Finnhub uses {symbol} for US; for NSE use {symbol}:NSE format
        articles = fh.company_news(f"{symbol}:NSE", _from=from_date, to=today)
        if not articles:
            # Fallback: try without exchange suffix
            articles = fh.company_news(symbol, _from=from_date, to=today)
    except Exception as exc:
        logger.error("Finnhub news error for %s: %s", symbol, exc)
        return {}

    if not articles:
        logger.info("No news for %s in last %d days", symbol, days_back)

    news_items: list[dict] = []
    scores: list[float] = []

    for article in articles[:30]:  # cap at 30 articles
        headline = article.get("headline", "")
        summary  = article.get("summary", "")
        source   = article.get("source", "")
        url      = article.get("url", "")
        ts       = article.get("datetime", 0)
        img      = article.get("image", "")

        score = _score_news(headline, summary)
        catalyst = _classify_catalyst(headline, summary)

        pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None

        news_items.append({
            "headline":   headline,
            "summary":    summary[:300],
            "source":     source,
            "url":        url,
            "published":  pub_dt,
            "score":      round(score, 4),
            "sentiment":  _classify(score),
            "catalyst":   catalyst,
        })
        scores.append(score)

    # Sort by publication time descending
    news_items.sort(key=lambda x: x.get("published") or "", reverse=True)

    avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    news_score_normalized = round((avg_score + 1.0) / 2.0, 4)

    # Check for high-impact catalysts in recent 48h
    recent_catalysts = [
        n["catalyst"] for n in news_items
        if n.get("published", "") >= (datetime.now(tz=timezone.utc) - timedelta(hours=48)).isoformat()
    ]
    has_earnings_news    = "EARNINGS" in recent_catalysts
    has_regulatory_risk  = "REGULATORY" in recent_catalysts
    has_major_contract   = "CONTRACT" in recent_catalysts

    payload = {
        "symbol":              symbol,
        "timestamp":           datetime.now(tz=timezone.utc).isoformat(),
        "article_count":       len(news_items),
        "avg_score":           avg_score,
        "news_score":          news_score_normalized,  # 0–1, 0.5 = neutral
        "overall_sentiment":   _classify(avg_score),
        "has_earnings_news":   has_earnings_news,
        "has_regulatory_risk": has_regulatory_risk,
        "has_major_contract":  has_major_contract,
        "recent_catalysts":    list(set(recent_catalysts)),
        "articles":            news_items,
    }

    out_path = NEWS_DIR / f"{symbol}_news.json"
    out_path.write_text(json.dumps(payload, indent=2))
    logger.info("Wrote news for %s | articles=%d score=%.3f", symbol, len(news_items), news_score_normalized)
    return payload


def run(symbols: list[str]) -> dict[str, dict]:
    results = {}
    for sym in symbols:
        try:
            results[sym] = collect_news(sym)
        except Exception as exc:
            logger.error("News failed %s: %s", sym, exc)
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    syms = sys.argv[1:] or ["RELIANCE", "TCS"]
    run(syms)

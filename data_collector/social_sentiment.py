"""
Multi-source social sentiment collector — free, no paid API keys required.

Sources:
  1. StockTwits  — stock-specific social posts with explicit Bullish/Bearish tags (free, no auth)
  2. Google News RSS  — news headlines for the stock (free, no key)
  3. Economic Times RSS — Indian financial news headlines (free, no key)

Output: data/sentiment/{SYMBOL}_sent.json  (same schema as before)
"""

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

SENTIMENT_DIR = Path("data/sentiment")
SENTIMENT_DIR.mkdir(parents=True, exist_ok=True)

_vader = SentimentIntensityAnalyzer()
_http  = httpx.Client(
    timeout=10,
    headers={"User-Agent": "Mozilla/5.0 (compatible; IndiaAutoTrader/1.0)"},
    follow_redirects=True,
)

MAX_STOCKTWITS = 30
MAX_NEWS_ITEMS  = 15


# ── helpers ───────────────────────────────────────────────────────────────────

def _score(text: str) -> float:
    return _vader.polarity_scores(text)["compound"]

def _classify(score: float) -> str:
    if score >= 0.05:  return "POSITIVE"
    if score <= -0.05: return "NEGATIVE"
    return "NEUTRAL"

def _parse_rss(xml_text: str) -> list[str]:
    """Extract <title> strings from RSS XML using stdlib — no extra dependency."""
    titles: list[str] = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            t = item.findtext("title")
            if t:
                titles.append(t.strip())
    except ET.ParseError:
        pass
    return titles


# ── source 1: StockTwits ──────────────────────────────────────────────────────

def _stocktwits(symbol: str) -> list[dict]:
    """
    Free public StockTwits stream — no auth needed.
    Tries NSE-suffixed symbol first (e.g. RELIANCE.NSE), then bare symbol.
    Also reads explicit Bullish/Bearish tags users attach to their posts.
    """
    for sym_fmt in [f"{symbol}.NSE", symbol]:
        try:
            r = _http.get(
                f"https://api.stocktwits.com/api/2/streams/symbol/{sym_fmt}.json"
            )
            if r.status_code != 200:
                continue
            messages = r.json().get("messages", [])
            items: list[dict] = []
            for m in messages:
                text  = m.get("body", "")
                score = _score(text)
                # Honour explicit user sentiment tag
                explicit = (m.get("entities") or {}).get("sentiment") or {}
                label    = explicit.get("basic", "")
                if label == "Bullish":
                    score = max(score, 0.25)
                elif label == "Bearish":
                    score = min(score, -0.25)
                items.append({"text": text[:200], "score": score, "source": "stocktwits"})
            if items:
                logger.info("StockTwits: %d posts for %s", len(items), symbol)
                return items[:MAX_STOCKTWITS]
        except Exception as exc:
            logger.debug("StockTwits error %s: %s", symbol, exc)
    return []


# ── source 2: Google News RSS ─────────────────────────────────────────────────

def _google_news(symbol: str, company_name: str = "") -> list[dict]:
    """Google News RSS — free, no API key, India edition."""
    query = f"{company_name or symbol} NSE stock"
    url   = (
        f"https://news.google.com/rss/search"
        f"?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    )
    try:
        r      = _http.get(url)
        titles = _parse_rss(r.text)[:MAX_NEWS_ITEMS]
        return [{"text": t, "score": _score(t), "source": "google_news"} for t in titles]
    except Exception as exc:
        logger.debug("Google News error %s: %s", symbol, exc)
        return []


# ── source 3: Economic Times RSS ─────────────────────────────────────────────

def _economic_times(symbol: str) -> list[dict]:
    """ET company news RSS — free, no key, very relevant for NSE stocks."""
    url = f"https://economictimes.indiatimes.com/{symbol.lower()}/rssfeeds/news.cms"
    try:
        r = _http.get(url)
        if r.status_code != 200:
            return []
        titles = _parse_rss(r.text)[:MAX_NEWS_ITEMS]
        return [{"text": t, "score": _score(t), "source": "economic_times"} for t in titles]
    except Exception as exc:
        logger.debug("ET RSS error %s: %s", symbol, exc)
        return []


# ── aggregate ─────────────────────────────────────────────────────────────────

def collect_sentiment(symbol: str, company_name: str = "") -> dict:
    all_items: list[dict] = []
    all_items += _stocktwits(symbol)
    all_items += _google_news(symbol, company_name)
    all_items += _economic_times(symbol)

    scores = [i["score"] for i in all_items]

    if scores:
        raw_avg  = sum(scores) / len(scores)
        positive = sum(1 for s in scores if s >  0.05) / len(scores)
        negative = sum(1 for s in scores if s < -0.05) / len(scores)
        neutral  = 1.0 - positive - negative
    else:
        raw_avg = positive = negative = 0.0
        neutral = 0.0

    sentiment_score = round((raw_avg + 1.0) / 2.0, 4)  # normalised 0–1, 0.5=neutral

    payload = {
        "symbol":          symbol,
        "timestamp":       datetime.now(tz=timezone.utc).isoformat(),
        "sources": {
            "stocktwits":     sum(1 for i in all_items if i["source"] == "stocktwits"),
            "google_news":    sum(1 for i in all_items if i["source"] == "google_news"),
            "economic_times": sum(1 for i in all_items if i["source"] == "economic_times"),
        },
        "total_items":     len(all_items),
        "raw_avg_score":   round(raw_avg, 4),
        "sentiment_score": sentiment_score,
        "sentiment_label": _classify(raw_avg),
        "positive_pct":    round(positive * 100, 1),
        "negative_pct":    round(negative * 100, 1),
        "neutral_pct":     round(neutral  * 100, 1),
        "sample_items":    all_items[:10],
    }

    out = SENTIMENT_DIR / f"{symbol}_sent.json"
    out.write_text(json.dumps(payload, indent=2))
    logger.info(
        "Sentiment %s | score=%.3f | items=%d (ST=%d GN=%d ET=%d)",
        symbol, sentiment_score, len(all_items),
        payload["sources"]["stocktwits"],
        payload["sources"]["google_news"],
        payload["sources"]["economic_times"],
    )
    return payload


def run(symbol_company_map: dict[str, str]) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for sym, company in symbol_company_map.items():
        try:
            results[sym] = collect_sentiment(sym, company)
        except Exception as exc:
            logger.error("Sentiment failed %s: %s", sym, exc)
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    test_map = {
        "RELIANCE": "Reliance Industries",
        "TCS":      "Tata Consultancy Services",
        "INFY":     "Infosys",
    }
    if len(sys.argv) > 1:
        test_map = {s: "" for s in sys.argv[1:]}
    run(test_map)

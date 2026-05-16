"""
Multi-source social sentiment collector — free, no paid API keys required.

Sources:
  1. Google News RSS      — news headlines, India edition (free, no key)
  2. Moneycontrol RSS     — Indian market news (free, no key)
  3. Business Standard RSS — Indian financial news (free, no key)
  4. Economic Times RSS   — general markets feed filtered by symbol (free, no key)

Output: data/sentiment/{SYMBOL}_sent.json
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
    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"},
    follow_redirects=True,
)

MAX_NEWS_ITEMS = 15


# ── helpers ───────────────────────────────────────────────────────────────────

def _score(text: str) -> float:
    return _vader.polarity_scores(text)["compound"]

def _classify(score: float) -> str:
    if score >= 0.05:  return "POSITIVE"
    if score <= -0.05: return "NEGATIVE"
    return "NEUTRAL"

def _parse_rss(xml_text: str) -> list[str]:
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

def _fetch_rss(url: str) -> list[str]:
    try:
        r = _http.get(url)
        if r.status_code == 200:
            return _parse_rss(r.text)
    except Exception as exc:
        logger.debug("RSS fetch error %s: %s", url, exc)
    return []

def _filter_relevant(titles: list[str], symbol: str, company_name: str) -> list[str]:
    """Keep only titles that mention the symbol or company name."""
    keywords = {symbol.lower()}
    if company_name:
        # Add each word of the company name that's >3 chars
        keywords.update(w.lower() for w in company_name.split() if len(w) > 3)
    return [t for t in titles if any(k in t.lower() for k in keywords)]


# ── source 1: Google News RSS ─────────────────────────────────────────────────

def _google_news(symbol: str, company_name: str = "") -> list[dict]:
    query = f"{company_name or symbol} NSE stock"
    url   = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    titles = _fetch_rss(url)[:MAX_NEWS_ITEMS]
    return [{"text": t, "score": _score(t), "source": "google_news"} for t in titles]


# ── source 2: Moneycontrol RSS ────────────────────────────────────────────────

_MC_FEEDS = [
    "https://www.moneycontrol.com/rss/marketsnews.xml",
    "https://www.moneycontrol.com/rss/business.xml",
]

def _moneycontrol(symbol: str, company_name: str = "") -> list[dict]:
    items: list[dict] = []
    for url in _MC_FEEDS:
        titles = _filter_relevant(_fetch_rss(url), symbol, company_name)
        items += [{"text": t, "score": _score(t), "source": "moneycontrol"} for t in titles]
    return items[:MAX_NEWS_ITEMS]


# ── source 3: LiveMint RSS ────────────────────────────────────────────────────

_MINT_FEEDS = [
    "https://www.livemint.com/rss/markets",
    "https://www.livemint.com/rss/companies",
]

def _livemint(symbol: str, company_name: str = "") -> list[dict]:
    items: list[dict] = []
    for url in _MINT_FEEDS:
        titles = _filter_relevant(_fetch_rss(url), symbol, company_name)
        items += [{"text": t, "score": _score(t), "source": "livemint"} for t in titles]
    return items[:MAX_NEWS_ITEMS]


# ── source 4: Economic Times RSS ─────────────────────────────────────────────

_ET_FEEDS = [
    "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
]

def _economic_times(symbol: str, company_name: str = "") -> list[dict]:
    items: list[dict] = []
    for url in _ET_FEEDS:
        titles = _filter_relevant(_fetch_rss(url), symbol, company_name)
        items += [{"text": t, "score": _score(t), "source": "economic_times"} for t in titles]
    return items[:MAX_NEWS_ITEMS]


# ── aggregate ─────────────────────────────────────────────────────────────────

def collect_sentiment(symbol: str, company_name: str = "") -> dict:
    all_items: list[dict] = []
    all_items += _google_news(symbol, company_name)
    all_items += _moneycontrol(symbol, company_name)
    all_items += _livemint(symbol, company_name)
    all_items += _economic_times(symbol, company_name)

    scores = [i["score"] for i in all_items]

    if scores:
        raw_avg  = sum(scores) / len(scores)
        positive = sum(1 for s in scores if s >  0.05) / len(scores)
        negative = sum(1 for s in scores if s < -0.05) / len(scores)
        neutral  = 1.0 - positive - negative
    else:
        raw_avg = positive = negative = neutral = 0.0

    sentiment_score = round((raw_avg + 1.0) / 2.0, 4)

    payload = {
        "symbol":          symbol,
        "timestamp":       datetime.now(tz=timezone.utc).isoformat(),
        "sources": {
            "google_news":    sum(1 for i in all_items if i["source"] == "google_news"),
            "moneycontrol":   sum(1 for i in all_items if i["source"] == "moneycontrol"),
            "livemint":       sum(1 for i in all_items if i["source"] == "livemint"),
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
        "Sentiment %s | score=%.3f | items=%d (GN=%d MC=%d LM=%d ET=%d)",
        symbol, sentiment_score, len(all_items),
        payload["sources"]["google_news"],
        payload["sources"]["moneycontrol"],
        payload["sources"]["livemint"],
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

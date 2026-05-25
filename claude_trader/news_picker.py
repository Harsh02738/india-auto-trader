"""
Pre-market news fetcher.

Pulls headlines from ET Markets RSS, Moneycontrol RSS, and NSE corporate
announcements. Returns structured list for Claude Code to analyze and pick stocks.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

_ET_RSS   = "https://economictimes.indiatimes.com/markets/rss.cms"
_MC_RSS   = "https://www.moneycontrol.com/rss/MCtopnews.xml"
_NSE_URL  = "https://www.nseindia.com/api/corporate-announcements?index=equities"

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}


def get_news_headlines(date: str | None = None) -> list[dict]:
    """
    Fetch top financial news headlines from three sources.

    Returns list of dicts with keys: source, headline, symbol_hint, url.
    Claude Code calls this MCP tool, reads the headlines, and decides which
    8 stocks to trade today.
    """
    if date is None:
        date = datetime.now(IST).strftime("%Y-%m-%d")

    headlines: list[dict] = []
    headlines += _fetch_rss(_ET_RSS, source="ET Markets", limit=12)
    headlines += _fetch_rss(_MC_RSS, source="Moneycontrol", limit=12)
    headlines += _fetch_nse_announcements(limit=16)

    logger.info("[NewsPicker] Fetched %d headlines for %s", len(headlines), date)
    return headlines[:40]


def _fetch_rss(url: str, source: str, limit: int = 15) -> list[dict]:
    try:
        import feedparser
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:limit]:
            results.append({
                "source": source,
                "headline": entry.get("title", ""),
                "summary": entry.get("summary", "")[:200],
                "url": entry.get("link", ""),
                "symbol_hint": None,
            })
        return results
    except Exception as exc:
        logger.warning("[NewsPicker] RSS fetch failed (%s): %s", url, exc)
        return []


def _fetch_nse_announcements(limit: int = 15) -> list[dict]:
    try:
        import requests
        resp = requests.get(_NSE_URL, headers=_NSE_HEADERS, timeout=8)
        resp.raise_for_status()
        items = resp.json()
        if isinstance(items, dict):
            items = items.get("data", [])
        results = []
        for item in items[:limit]:
            symbol = item.get("symbol") or item.get("sm_isin") or ""
            subject = item.get("subject") or item.get("desc") or ""
            results.append({
                "source": "NSE Announcements",
                "headline": f"{symbol}: {subject}",
                "summary": item.get("attchmntText", "")[:200],
                "url": "",
                "symbol_hint": symbol.upper() if symbol else None,
            })
        return results
    except Exception as exc:
        logger.warning("[NewsPicker] NSE fetch failed: %s", exc)
        return []

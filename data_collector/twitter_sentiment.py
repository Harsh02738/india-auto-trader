"""
Collects X/Twitter sentiment via Tweepy X API v2.
Uses VaderSentiment for NLP scoring.
Writes data/sentiment/{SYMBOL}_sent.json.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import tweepy
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from config.settings import settings

logger = logging.getLogger(__name__)

SENTIMENT_DIR = Path("data/sentiment")
SENTIMENT_DIR.mkdir(parents=True, exist_ok=True)

_vader = SentimentIntensityAnalyzer()

# X/Twitter search queries per symbol (extend as needed)
SYMBOL_QUERIES: dict[str, str] = {}  # populated dynamically

# How many recent tweets to sample per symbol
MAX_TWEETS = 50

# Influential finance accounts to weight more heavily (optional)
INFLUENTIAL_ACCOUNTS: set[str] = {
    "moneycontrol", "economictimes", "bseindia", "NSEindia",
    "zerodhaonline", "AngelOne_Ltd", "nirmalasite",
}


def _tweepy_client() -> tweepy.Client | None:
    if not settings.twitter_bearer_token:
        logger.warning("TWITTER_BEARER_TOKEN not set — skipping Twitter sentiment")
        return None
    return tweepy.Client(
        bearer_token=settings.twitter_bearer_token,
        wait_on_rate_limit=True,
    )


def _build_query(symbol: str, company_name: str = "") -> str:
    """Build a Twitter search query for a stock symbol."""
    base = f"${symbol} OR #{symbol}"
    if company_name:
        base += f' OR "{company_name}"'
    # Filter out noise
    base += " -is:retweet lang:en"
    return base


def _score_tweet(text: str) -> float:
    """Return compound Vader score (-1 to +1)."""
    return _vader.polarity_scores(text)["compound"]


def _classify(score: float) -> str:
    if score >= 0.05:
        return "POSITIVE"
    if score <= -0.05:
        return "NEGATIVE"
    return "NEUTRAL"


def collect_sentiment(symbol: str, company_name: str = "") -> dict:
    client = _tweepy_client()
    query = _build_query(symbol, company_name)

    tweets_data: list[dict] = []
    scores: list[float] = []

    if client:
        try:
            response = client.search_recent_tweets(
                query=query,
                max_results=MAX_TWEETS,
                tweet_fields=["created_at", "author_id", "public_metrics", "text"],
                expansions=["author_id"],
                user_fields=["username", "public_metrics"],
            )

            # Build author_id → username map
            users: dict[str, str] = {}
            if response.includes and response.includes.get("users"):
                for u in response.includes["users"]:
                    users[str(u.id)] = u.username

            if response.data:
                for tweet in response.data:
                    text = tweet.text
                    score = _score_tweet(text)
                    author = users.get(str(tweet.author_id), "unknown")
                    metrics = tweet.public_metrics or {}

                    # Weight influential accounts slightly higher
                    weight = 1.5 if author.lower() in INFLUENTIAL_ACCOUNTS else 1.0
                    # Weight high-engagement tweets more
                    engagement = (metrics.get("like_count", 0) + metrics.get("retweet_count", 0))
                    if engagement > 100:
                        weight *= 1.3

                    scores.append(score * weight)
                    tweets_data.append({
                        "text":       text[:200],
                        "score":      round(score, 4),
                        "sentiment":  _classify(score),
                        "author":     author,
                        "likes":      metrics.get("like_count", 0),
                        "retweets":   metrics.get("retweet_count", 0),
                        "created_at": str(tweet.created_at),
                    })

        except tweepy.TweepyException as exc:
            logger.error("Twitter API error for %s: %s", symbol, exc)

    # Aggregate
    if scores:
        raw_avg = sum(scores) / len(scores)
        positive = sum(1 for s in scores if s > 0.05) / len(scores)
        negative = sum(1 for s in scores if s < -0.05) / len(scores)
        neutral  = 1.0 - positive - negative
    else:
        raw_avg = 0.0
        positive = negative = neutral = 0.0

    # Normalize to 0–1 for composite scoring (0.5 = neutral)
    sentiment_score = round((raw_avg + 1.0) / 2.0, 4)

    payload = {
        "symbol":           symbol,
        "timestamp":        datetime.now(tz=timezone.utc).isoformat(),
        "tweet_count":      len(tweets_data),
        "raw_avg_score":    round(raw_avg, 4),
        "sentiment_score":  sentiment_score,   # 0–1, 0.5 = neutral
        "sentiment_label":  _classify(raw_avg),
        "positive_pct":     round(positive * 100, 1),
        "negative_pct":     round(negative * 100, 1),
        "neutral_pct":      round(neutral * 100, 1),
        "sample_tweets":    tweets_data[:10],   # first 10 for reference
    }

    out_path = SENTIMENT_DIR / f"{symbol}_sent.json"
    out_path.write_text(json.dumps(payload, indent=2))
    logger.info("Wrote sentiment for %s | score=%.3f | tweets=%d", symbol, sentiment_score, len(tweets_data))
    return payload


def run(symbol_company_map: dict[str, str]) -> dict[str, dict]:
    results = {}
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
        "TCS": "Tata Consultancy Services",
        "INFY": "Infosys",
    }
    if len(sys.argv) > 1:
        test_map = {s: "" for s in sys.argv[1:]}
    run(test_map)

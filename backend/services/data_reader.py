"""Reads pre-computed JSON files from the data/ directory."""

import json
from pathlib import Path


def _read(path: Path) -> dict | list | None:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


# ── Equity ────────────────────────────────────────────────────────────────────

def get_ohlcv(symbol: str) -> dict | None:
    return _read(Path(f"data/market/{symbol}_ohlcv.json"))


def get_fundamentals(symbol: str) -> dict | None:
    return _read(Path(f"data/fundamentals/{symbol}_fund.json"))


def get_signal(symbol: str) -> dict | None:
    return _read(Path(f"data/signals/{symbol}_signal.json"))


def get_all_signals() -> list[dict]:
    signals = []
    for p in Path("data/signals").glob("*_signal.json"):
        data = _read(p)
        if data and isinstance(data, dict):
            signals.append(data)
    signals.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    return signals


# ── Sentiment ─────────────────────────────────────────────────────────────────

def get_sentiment(symbol: str) -> dict | None:
    return _read(Path(f"data/sentiment/{symbol}_sent.json"))


def get_news(symbol: str) -> dict | None:
    return _read(Path(f"data/news/{symbol}_news.json"))


def get_fii_dii() -> dict | None:
    return _read(Path("data/sentiment/fii_dii.json"))


# ── Options ───────────────────────────────────────────────────────────────────

def get_option_chain(symbol: str) -> dict | None:
    return _read(Path(f"data/options/{symbol}_chain.json"))


def get_oi_data(symbol: str) -> dict | None:
    return _read(Path(f"data/options/{symbol}_oi.json"))


def get_market_pcr() -> dict | None:
    return _read(Path("data/options/market_pcr.json"))


# ── Earnings ──────────────────────────────────────────────────────────────────

def get_earnings_calendar() -> dict | None:
    return _read(Path("data/earnings/calendar.json"))


def get_earnings_results(symbol: str) -> dict | None:
    return _read(Path(f"data/earnings/{symbol}_results.json"))


# ── Penny ─────────────────────────────────────────────────────────────────────

def get_penny_candidates() -> dict | None:
    return _read(Path("data/penny/candidates.json"))


# ── Portfolio ─────────────────────────────────────────────────────────────────

def get_portfolio_snapshot() -> dict | None:
    return _read(Path("data/portfolio/snapshot.json"))

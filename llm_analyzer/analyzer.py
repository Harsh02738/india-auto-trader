"""
Free LLM analysis vote for the consensus engine.

Provider priority: Groq (primary) → Cerebras (fallback) → skip.
Both use OpenAI-compatible /v1/chat/completions endpoints.
Get free keys at: https://console.groq.com  /  https://cloud.cerebras.ai
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import httpx

from strategies.base import StrategySignal

logger = logging.getLogger(__name__)

_GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
_CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"

_GROQ_MODEL     = "llama-3.3-70b-versatile"
_CEREBRAS_MODEL = "llama3.1-70b"

_TIMEOUT = 8.0   # never block the consensus engine

_RATE_WINDOW = 60
_RATE_MAX    = 30  # max LLM calls per 60-second window

_SYSTEM_PROMPT = (
    "You are an expert NSE/BSE quantitative analyst. "
    "Given market data for an Indian equity, output a trading signal.\n"
    "Rules:\n"
    "- action must be BUY, SELL, or HOLD\n"
    "- confidence: 0.0–1.0 (certainty of the signal)\n"
    "- reasoning: one concise sentence (≤80 chars)\n"
    "- Only BUY if technicals and fundamentals strongly support it (score ≥ 0.65)\n"
    "- Only SELL if bearish signals dominate (score ≤ 0.35)\n"
    "- Default to HOLD when uncertain\n"
    'Respond with ONLY valid JSON: {"action": "...", "confidence": 0.0, "reasoning": "..."}'
)


class LLMAnalyzer:
    """Calls a free LLM API and returns a StrategySignal for the consensus engine."""

    _call_times: list[float] = []  # class-level rate limiter shared across all instances

    def analyze(
        self,
        symbol: str,
        ohlcv: dict,
        fundamentals: Optional[dict] = None,
        sentiment: Optional[dict] = None,
        news: Optional[list] = None,
    ) -> Optional[StrategySignal]:
        """
        Returns a StrategySignal or None if the LLM is unavailable or rate-limited.
        Never raises — safe to call from inside the consensus engine.
        """
        if not self._rate_ok():
            logger.debug("[LLM] Rate limit reached — skipping %s", symbol)
            return None

        user_msg = self._build_payload(symbol, ohlcv, fundamentals, sentiment, news)
        raw = self._call_provider(user_msg)
        if raw is None:
            return None

        return self._parse_signal(raw, ohlcv)

    # ── Rate limiting ──────────────────────────────────────────────────────────

    def _rate_ok(self) -> bool:
        now = time.monotonic()
        LLMAnalyzer._call_times = [t for t in LLMAnalyzer._call_times if now - t < _RATE_WINDOW]
        if len(LLMAnalyzer._call_times) >= _RATE_MAX:
            return False
        LLMAnalyzer._call_times.append(now)
        return True

    # ── Payload builder ────────────────────────────────────────────────────────

    @staticmethod
    def _build_payload(
        symbol: str,
        ohlcv: dict,
        fundamentals: Optional[dict],
        sentiment: Optional[dict],
        news: Optional[list],
    ) -> str:
        data: dict = {
            "symbol": symbol,
            "technical": {
                "rsi": ohlcv.get("rsi"),
                "macd_crossover": ohlcv.get("macd_crossover"),
                "macd_hist": ohlcv.get("macd_hist"),
                "above_ema200": ohlcv.get("above_ema200"),
                "bb_pct": ohlcv.get("bb_pct"),
                "vol_ratio": ohlcv.get("vol_ratio"),
                "last_close": ohlcv.get("last_close"),
                "atr": ohlcv.get("atr"),
            },
        }
        if fundamentals:
            data["fundamental"] = {
                "pe_ratio": fundamentals.get("pe_ratio"),
                "roe": fundamentals.get("roe"),
                "de_ratio": fundamentals.get("de_ratio"),
                "revenue_growth_yoy": fundamentals.get("revenue_growth_yoy"),
                "fundamental_score": fundamentals.get("fundamental_score"),
            }
        if sentiment:
            data["sentiment"] = {
                "twitter_score": sentiment.get("twitter_score"),
                "news_sentiment": sentiment.get("news_sentiment"),
                "fii_net_cr": sentiment.get("fii_net_cr"),
            }
        if news:
            data["catalysts"] = [
                {
                    "headline": n.get("headline", ""),
                    "impact": n.get("impact", ""),
                    "days_to_event": n.get("days_to_event"),
                }
                for n in news[:3]
            ]
        return json.dumps(data, default=str)

    # ── Provider call ──────────────────────────────────────────────────────────

    @staticmethod
    def _call_provider(user_msg: str) -> Optional[dict]:
        groq_key     = os.environ.get("GROQ_API_KEY", "")
        cerebras_key = os.environ.get("CEREBRAS_API_KEY", "")

        providers = []
        if groq_key:
            providers.append((_GROQ_URL, groq_key, _GROQ_MODEL))
        if cerebras_key:
            providers.append((_CEREBRAS_URL, cerebras_key, _CEREBRAS_MODEL))

        if not providers:
            logger.debug("[LLM] No API keys configured (GROQ_API_KEY / CEREBRAS_API_KEY) — skipping vote")
            return None

        body = {
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 120,
        }

        for url, key, model in providers:
            body["model"] = model
            try:
                resp = httpx.post(
                    url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    timeout=_TIMEOUT,
                )
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    return json.loads(content)
                logger.debug("[LLM] %s returned HTTP %s", url, resp.status_code)
            except Exception as exc:
                logger.debug("[LLM] %s error: %s", url, exc)

        return None

    # ── Signal parser ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_signal(raw: dict, ohlcv: dict) -> Optional[StrategySignal]:
        action     = str(raw.get("action", "HOLD")).upper()
        confidence = float(raw.get("confidence", 0.0))
        reasoning  = str(raw.get("reasoning", "LLM analysis"))

        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"
        confidence = max(0.0, min(1.0, confidence))

        entry = float(ohlcv.get("last_close") or 0)
        atr   = float(ohlcv.get("atr") or entry * 0.02)

        if action == "HOLD" or entry <= 0:
            return StrategySignal(
                action="HOLD",
                confidence=0.0,
                entry=entry,
                stop_loss=entry,
                target=entry,
                risk_reward=0.0,
                reasoning=reasoning,
            )

        if action == "BUY":
            sl = round(entry - 1.5 * atr, 2)
            tg = round(entry + 2.5 * atr, 2)
        else:
            sl = round(entry + 1.5 * atr, 2)
            tg = round(entry - 2.5 * atr, 2)

        rr = round(abs(tg - entry) / max(abs(entry - sl), 0.01), 2)
        return StrategySignal(
            action=action,
            confidence=confidence,
            entry=entry,
            stop_loss=sl,
            target=tg,
            risk_reward=rr,
            reasoning=reasoning,
        )

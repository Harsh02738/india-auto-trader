"""
Strategy Consensus Engine.

Runs all 6 quantitative strategies + optional existing 4-factor composite score,
then returns a consensus signal if ≥ MIN_VOTES strategies agree on direction.

Usage:
    engine = StrategyEngine()
    signal = engine.evaluate("RELIANCE", ohlcv_dict, fundamentals_dict)
    if signal.action != "HOLD":
        # execute trade
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .base import Action, BaseStrategy, StrategySignal
from .momentum import MomentumStrategy
from .mean_reversion import MeanReversionStrategy
from .macd_rsi_confluence import MacdRsiConfluenceStrategy
from .supertrend import SupertrendStrategy
from .vwap_reversion import VwapReversionStrategy
from .bollinger_squeeze import BollingerSqueezeStrategy
from .orb import ORBStrategy
from .gap_and_go import GapAndGoStrategy
from .cpr import CPRStrategy
from .ema_stack import EMAStackStrategy

logger = logging.getLogger(__name__)

# Minimum number of strategies that must agree for a valid signal.
# Raised to 3 for intraday paper trading (tighter filter, 10 strategies total).
MIN_VOTES = 3

# LLM signal counts as a vote only when its confidence meets this threshold
_LLM_MIN_CONFIDENCE = 0.60


@dataclass
class ConsensusSignal:
    symbol: str
    action: Action
    combined_confidence: float        # average confidence of agreeing strategies
    vote_count: int                   # how many strategies agree
    total_strategies: int             # total strategies evaluated
    agreeing_strategies: list[str]    # names of agreeing strategies
    entry: float
    stop_loss: float
    target: float
    risk_reward: float
    individual_signals: dict[str, StrategySignal]   # name → signal
    reasoning: str
    llm_action: str = "HOLD"          # Free LLM analysis vote
    llm_score: float = 0.0            # LLM confidence 0-1
    llm_matched: bool = False         # True if LLM agreed with consensus direction


class StrategyEngine:
    """
    Evaluates a stock against all strategies and returns a consensus verdict.
    Also reads pre-computed composite score from the signal file (if available)
    and includes it as an additional vote.
    """

    def __init__(self, min_votes: int = MIN_VOTES) -> None:
        self.min_votes = min_votes
        self._strategies: list[BaseStrategy] = [
            MomentumStrategy(),
            MeanReversionStrategy(),
            MacdRsiConfluenceStrategy(),
            SupertrendStrategy(),
            VwapReversionStrategy(),
            BollingerSqueezeStrategy(),
            # New intraday strategies for NSE paper trading
            ORBStrategy(),
            GapAndGoStrategy(),
            CPRStrategy(),
            EMAStackStrategy(),
        ]

    def evaluate(
        self,
        symbol: str,
        ohlcv: dict,
        fundamentals: dict | None = None,
        use_llm: bool = True,
    ) -> ConsensusSignal:
        """
        Run all strategies and return a ConsensusSignal.
        Returns action=HOLD if fewer than min_votes strategies agree.

        A free LLM analysis (Groq/Cerebras) is fetched as an optional 8th vote.
        LLM only counts if its confidence >= _LLM_MIN_CONFIDENCE AND at least one
        existing strategy already votes in the same direction (never sole trigger).
        """
        individual: dict[str, StrategySignal] = {}
        for strat in self._strategies:
            try:
                sig = strat.generate_signal(ohlcv, fundamentals)
                individual[strat.name] = sig
            except Exception as exc:
                logger.warning("[%s] %s error: %s", symbol, strat.name, exc)

        # Include 4-factor composite score as an extra vote (weight = 1 vote)
        composite_vote = self._composite_vote(symbol, ohlcv, fundamentals)
        if composite_vote is not None:
            individual["Composite4F"] = composite_vote

        # Free LLM analysis vote (optional 8th signal)
        llm_action = "HOLD"
        llm_score = 0.0
        llm_vote: StrategySignal | None = None
        if use_llm:
            llm_action, llm_score, llm_vote = self._llm_vote(symbol, ohlcv, fundamentals, individual)
            if llm_vote is not None:
                individual["LLMAnalysis"] = llm_vote

        total = len(individual)
        entry = ohlcv.get("last_close", 0)

        # Count votes per direction
        buy_signals  = {n: s for n, s in individual.items() if s.action == "BUY"}
        sell_signals = {n: s for n, s in individual.items() if s.action == "SELL"}

        best_action: Action
        agreeing: dict[str, StrategySignal]

        if len(buy_signals) >= len(sell_signals):
            best_action = "BUY"
            agreeing    = buy_signals
        else:
            best_action = "SELL"
            agreeing    = sell_signals

        vote_count = len(agreeing)
        llm_matched = llm_action == best_action and llm_action != "HOLD"

        if vote_count < self.min_votes:
            return ConsensusSignal(
                symbol=symbol,
                action="HOLD",
                combined_confidence=0.0,
                vote_count=vote_count,
                total_strategies=total,
                agreeing_strategies=list(agreeing.keys()),
                entry=entry,
                stop_loss=entry,
                target=entry,
                risk_reward=0.0,
                individual_signals=individual,
                reasoning=(
                    f"Only {vote_count}/{total} strategies agree — "
                    f"minimum {self.min_votes} required for trade"
                ),
                llm_action=llm_action,
                llm_score=llm_score,
                llm_matched=llm_matched,
            )

        # Aggregate entry, stop, target from agreeing strategies
        combined_conf = sum(s.confidence for s in agreeing.values()) / vote_count
        avg_entry     = entry  # always use live price
        avg_sl        = sum(s.stop_loss for s in agreeing.values()) / vote_count
        avg_target    = sum(s.target for s in agreeing.values()) / vote_count
        avg_rr        = (avg_target - avg_entry) / max(avg_entry - avg_sl, 0.01)

        strategy_names = sorted(agreeing.keys())
        reasoning_parts = [f"{n}({s.confidence:.2f})" for n, s in agreeing.items()]
        llm_note = f" | LLM:{llm_action}({llm_score:.0%})" if llm_action != "HOLD" else ""
        reasoning = (
            f"{vote_count}/{total} strategies agree {best_action}: "
            + ", ".join(reasoning_parts)
            + llm_note
        )

        return ConsensusSignal(
            symbol=symbol,
            action=best_action,
            combined_confidence=round(combined_conf, 3),
            vote_count=vote_count,
            total_strategies=total,
            agreeing_strategies=strategy_names,
            entry=round(avg_entry, 2),
            stop_loss=round(avg_sl, 2),
            target=round(avg_target, 2),
            risk_reward=round(avg_rr, 2),
            individual_signals=individual,
            reasoning=reasoning,
            llm_action=llm_action,
            llm_score=round(llm_score, 3),
            llm_matched=llm_matched,
        )

    @staticmethod
    def _llm_vote(
        symbol: str,
        ohlcv: dict,
        fundamentals: dict | None,
        existing_signals: dict[str, StrategySignal],
    ) -> tuple[str, float, StrategySignal | None]:
        """
        Call a free LLM API (Groq/Cerebras) and convert its response to a StrategySignal.
        LLM only gets a vote if:
          1. Its confidence >= _LLM_MIN_CONFIDENCE
          2. At least one existing strategy already votes in the same direction
             (LLM confirms, never initiates a signal on its own)
        Returns (llm_action, llm_score, signal_or_None).
        """
        try:
            import json
            from pathlib import Path
            from llm_analyzer.analyzer import LLMAnalyzer

            # Load sentiment and news data if available
            sentiment: dict | None = None
            sent_path = Path(f"data/sentiment/{symbol}_sent.json")
            if sent_path.exists():
                try:
                    sentiment = json.loads(sent_path.read_text())
                except Exception:
                    pass

            news: list | None = None
            news_path = Path(f"data/news/{symbol}_news.json")
            if news_path.exists():
                try:
                    raw_news = json.loads(news_path.read_text())
                    news = raw_news if isinstance(raw_news, list) else raw_news.get("items", [])
                except Exception:
                    pass

            analyzer = LLMAnalyzer()
            signal = analyzer.analyze(symbol, ohlcv, fundamentals, sentiment, news)

            if signal is None or signal.action == "HOLD":
                return signal.action if signal else "HOLD", 0.0, None

            llm_action = signal.action
            llm_score = signal.confidence

            if llm_score < _LLM_MIN_CONFIDENCE:
                return llm_action, llm_score, None

            # Only add as a vote if at least one existing signal agrees
            existing_same_direction = [
                s for s in existing_signals.values() if s.action == llm_action
            ]
            if not existing_same_direction:
                logger.debug("[LLM] %s %s conf=%.2f but no existing strategy agrees — not voting",
                             symbol, llm_action, llm_score)
                return llm_action, llm_score, None

            return llm_action, llm_score, signal

        except Exception as exc:
            logger.debug("[LLM] %s vote skipped: %s", symbol, exc)
            return "HOLD", 0.0, None

    # ── 4-Factor Composite Score helper ───────────────────────────────────────

    @staticmethod
    def _composite_vote(
        symbol: str,
        ohlcv: dict,
        fundamentals: dict | None,
    ) -> StrategySignal | None:
        """
        Read the pre-computed composite score from an existing signal file
        or quickly recompute it. Returns a StrategySignal or None.
        """
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return None

        # Try reading existing signal file
        sig_path = Path(f"data/signals/{symbol}_signal.json")
        composite = None
        if sig_path.exists():
            try:
                data = json.loads(sig_path.read_text())
                composite = data.get("composite_score")
            except Exception:
                pass

        # Quick inline composite if no file
        if composite is None:
            composite = _quick_composite(ohlcv, fundamentals)

        if composite is None:
            return None

        atr = ohlcv.get("atr") or entry * 0.02

        if composite >= 0.65:
            sl = round(entry - 1.5 * atr, 2)
            tg = round(entry + 2.5 * atr, 2)
            return StrategySignal(
                action="BUY",
                confidence=min(composite, 1.0),
                entry=entry,
                stop_loss=sl,
                target=tg,
                risk_reward=round((tg - entry) / max(entry - sl, 0.01), 2),
                reasoning=f"4-factor composite score {composite:.2f}",
            )
        if composite <= 0.40:
            sl = round(entry + 1.5 * atr, 2)
            tg = round(entry - 2.5 * atr, 2)
            return StrategySignal(
                action="SELL",
                confidence=min(1 - composite, 1.0),
                entry=entry,
                stop_loss=sl,
                target=tg,
                risk_reward=round((entry - tg) / max(sl - entry, 0.01), 2),
                reasoning=f"4-factor composite score {composite:.2f}",
            )
        return StrategySignal(
            action="HOLD",
            confidence=0.0,
            entry=entry,
            stop_loss=entry,
            target=entry,
            risk_reward=0.0,
            reasoning=f"4-factor composite score {composite:.2f} neutral",
        )


def _quick_composite(ohlcv: dict, fundamentals: dict | None) -> float | None:
    """Light-weight composite score using only available data fields."""
    tech = _tech_score(ohlcv)
    fund = _fund_score(fundamentals) if fundamentals else 0.5
    # Skip sentiment/news without live data
    return round(0.35 * tech + 0.30 * fund + 0.35 * 0.5, 4)


def _tech_score(d: dict) -> float:
    score = 0.15   # baseline
    rsi = d.get("rsi") or 50
    if rsi < 30:
        score += 0.30
    elif rsi < 40:
        score += 0.20
    elif rsi < 50:
        score += 0.10
    elif rsi > 75:
        score -= 0.20
    elif rsi > 65:
        score -= 0.10

    if d.get("macd_crossover"):
        score += 0.25
    elif (d.get("macd_hist") or 0) > 0:
        score += 0.15
    elif (d.get("macd_hist") or 0) < 0:
        score -= 0.10

    if d.get("above_ema200"):
        score += 0.20
    else:
        score -= 0.05

    vr = d.get("vol_ratio") or 1.0
    if vr >= 1.5:
        score += 0.15
    elif vr >= 1.2:
        score += 0.05

    bb = d.get("bb_pct") or 0.5
    if bb < 0.20:
        score += 0.10
    elif bb > 0.80:
        score -= 0.10

    return max(0.0, min(1.0, score))


def _fund_score(d: dict) -> float:
    score = d.get("fundamental_score")
    if score is not None:
        return float(score)
    # Rough approximation
    s = 0.5
    pe = d.get("pe_ratio")
    if pe and 0 < pe < 20:
        s += 0.10
    elif pe and pe > 50:
        s -= 0.10
    roe = d.get("roe")
    if roe and roe > 20:
        s += 0.10
    elif roe and roe < 10:
        s -= 0.10
    de = d.get("de_ratio")
    if de and de < 0.5:
        s += 0.10
    elif de and de > 2.0:
        s -= 0.20
    return max(0.0, min(1.0, s))

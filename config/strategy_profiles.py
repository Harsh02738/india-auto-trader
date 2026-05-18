from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class StrategyProfile:
    name: str
    mode: Literal["intraday", "swing"]

    # ── Hold time ──────────────────────────────────────────────────────────────
    max_hold_days: int                    # 0 = intraday only
    product_type: str                     # MIS (intraday) or CNC (delivery)

    # ── Signal thresholds ──────────────────────────────────────────────────────
    min_composite_score: float            # minimum score to enter
    min_rr_ratio: float                   # minimum reward:risk ratio

    # ── Stop-loss / target rules ───────────────────────────────────────────────
    atr_stop_multiplier: float            # stop = entry ± ATR × multiplier
    atr_target_multiplier: float          # initial target = entry ± ATR × this
    trailing_stop_atr_mult: float         # once target hit, trail by ATR × this

    # ── Position sizing ────────────────────────────────────────────────────────
    risk_pct_per_trade: float             # % of capital to risk
    max_position_pct: float              # max notional per position

    # ── Filter overrides ───────────────────────────────────────────────────────
    require_ema200_above: bool            # long only if price > EMA-200
    min_volume_ratio: float               # volume must be ≥ N× 20-day avg
    rsi_oversold_threshold: float         # don't buy if RSI above this (overbought)
    rsi_overbought_threshold: float       # don't short if RSI below this

    # ── Timing rules ───────────────────────────────────────────────────────────
    avoid_first_minutes: int              # skip first N minutes after open
    avoid_last_minutes: int               # square-off N minutes before close
    trade_expiry_week: bool               # allow trading in F&O expiry week
    trade_results_week: bool              # allow trading 2 days around results

    # ── Sector exposure ────────────────────────────────────────────────────────
    max_sector_exposure_pct: float
    max_concurrent_positions: int

    # ── Earnings strategy modifier ─────────────────────────────────────────────
    pre_earnings_size_pct: float          # % of normal size for pre-earnings entry
    post_earnings_wait_bars: int          # 5-min bars to wait after results out

    # ── Watchlist (can be overridden per profile) ──────────────────────────────
    watchlist_symbols: list[str] = field(default_factory=list)


INTRADAY_PROFILE = StrategyProfile(
    name="Intraday MIS",
    mode="intraday",
    max_hold_days=0,
    product_type="MIS",

    min_composite_score=0.65,
    min_rr_ratio=1.5,

    atr_stop_multiplier=1.5,
    atr_target_multiplier=2.5,
    trailing_stop_atr_mult=1.0,

    risk_pct_per_trade=0.02,
    max_position_pct=0.05,

    require_ema200_above=True,
    min_volume_ratio=1.2,
    rsi_oversold_threshold=65.0,    # buy only if RSI ≤ 65 (not overbought)
    rsi_overbought_threshold=35.0,  # short only if RSI ≥ 35

    avoid_first_minutes=15,         # skip 9:15–9:30 chaos
    avoid_last_minutes=20,          # square-off by 3:10 PM
    trade_expiry_week=False,        # smaller size in expiry week (handled elsewhere)
    trade_results_week=False,       # avoid results day entries

    max_sector_exposure_pct=0.30,
    max_concurrent_positions=5,

    pre_earnings_size_pct=0.50,     # half size pre-earnings
    post_earnings_wait_bars=6,      # wait 30 min after results (6 × 5-min bars)
)


SWING_PROFILE = StrategyProfile(
    name="Swing CNC",
    mode="swing",
    max_hold_days=10,
    product_type="CNC",

    min_composite_score=0.68,
    min_rr_ratio=2.0,

    atr_stop_multiplier=2.0,
    atr_target_multiplier=4.0,
    trailing_stop_atr_mult=1.5,

    risk_pct_per_trade=0.015,       # slightly lower risk per trade for overnight
    max_position_pct=0.05,

    require_ema200_above=True,
    min_volume_ratio=1.0,
    rsi_oversold_threshold=60.0,
    rsi_overbought_threshold=40.0,

    avoid_first_minutes=30,         # wait for opening direction to establish
    avoid_last_minutes=10,          # no new entries near close
    trade_expiry_week=False,
    trade_results_week=False,

    max_sector_exposure_pct=0.30,
    max_concurrent_positions=8,     # more positions for swing diversification

    pre_earnings_size_pct=0.50,
    post_earnings_wait_bars=12,     # wait 1 hour post-results for swing
)


# ── Momentum strategy profile (multi-week trend following) ────────────────────
MOMENTUM_PROFILE = StrategyProfile(
    name="Momentum Trend",
    mode="swing",
    max_hold_days=20,               # hold trends up to 1 month
    product_type="CNC",

    min_composite_score=0.65,
    min_rr_ratio=2.5,               # needs better R:R for trend following

    atr_stop_multiplier=2.0,        # wider stop to survive pullbacks
    atr_target_multiplier=5.0,      # big target — trend can run far
    trailing_stop_atr_mult=2.0,     # trail loosely to stay in trend

    risk_pct_per_trade=0.015,
    max_position_pct=0.05,

    require_ema200_above=True,
    min_volume_ratio=1.0,           # momentum doesn't need volume spike
    rsi_oversold_threshold=70.0,    # can buy higher RSI in strong trends
    rsi_overbought_threshold=40.0,

    avoid_first_minutes=30,
    avoid_last_minutes=10,
    trade_expiry_week=False,
    trade_results_week=False,

    max_sector_exposure_pct=0.30,
    max_concurrent_positions=6,

    pre_earnings_size_pct=0.50,
    post_earnings_wait_bars=12,
)


# ── Mean reversion profile (short pullback plays) ─────────────────────────────
MEAN_REVERSION_PROFILE = StrategyProfile(
    name="Mean Reversion",
    mode="intraday",
    max_hold_days=3,                # quick bounce plays
    product_type="MIS",

    min_composite_score=0.60,
    min_rr_ratio=1.5,

    atr_stop_multiplier=1.5,
    atr_target_multiplier=2.0,      # smaller target — bounce to mean
    trailing_stop_atr_mult=1.0,

    risk_pct_per_trade=0.02,
    max_position_pct=0.05,

    require_ema200_above=True,
    min_volume_ratio=1.0,
    rsi_oversold_threshold=50.0,    # RSI must be below 50 for mean reversion buys
    rsi_overbought_threshold=35.0,

    avoid_first_minutes=15,
    avoid_last_minutes=20,
    trade_expiry_week=False,
    trade_results_week=False,

    max_sector_exposure_pct=0.30,
    max_concurrent_positions=5,

    pre_earnings_size_pct=0.25,     # very small near earnings (mean reversion risky)
    post_earnings_wait_bars=6,
)

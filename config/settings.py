from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Kotak Neo ──────────────────────────────────────────────────────────────
    kotak_consumer_key: str = ""
    kotak_consumer_secret: str = ""
    kotak_mobile_number: str = ""
    kotak_ucc: str = ""
    kotak_mpin: str = ""
    kotak_totp_secret: str = ""
    kotak_environment: Literal["prod", "uat"] = "prod"

    # ── External APIs ──────────────────────────────────────────────────────────
    twitter_bearer_token: str = ""
    finnhub_api_key: str = ""
    alpha_vantage_key: str = ""

    # ── Telegram ───────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Infrastructure ─────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite+aiosqlite:///./data/trades/history.db"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:3000"]

    # ── Strategy mode ──────────────────────────────────────────────────────────
    strategy_mode: Literal["intraday", "swing"] = "intraday"

    # ── Position limits ────────────────────────────────────────────────────────
    max_positions: int = Field(default=5, ge=1, le=20)
    max_account_risk_pct: float = Field(default=0.02, ge=0.001, le=0.05)
    max_single_stock_pct: float = Field(default=0.05, ge=0.01, le=0.15)
    max_penny_stock_pct: float = Field(default=0.01, ge=0.001, le=0.02)
    max_fno_pct: float = Field(default=0.02, ge=0.001, le=0.05)
    max_sector_pct: float = Field(default=0.30, ge=0.10, le=0.50)

    # ── Circuit breaker thresholds ─────────────────────────────────────────────
    circuit_daily_loss_pct: float = Field(default=0.03, ge=0.01, le=0.10)
    circuit_consecutive_losses: int = Field(default=5, ge=3, le=10)
    circuit_max_drawdown_pct: float = Field(default=0.15, ge=0.05, le=0.30)

    # ── Signal confidence thresholds ───────────────────────────────────────────
    min_signal_confidence: float = Field(default=0.65, ge=0.50, le=0.90)
    min_fno_confidence: float = Field(default=0.70, ge=0.55, le=0.90)
    min_penny_confidence: float = Field(default=0.70, ge=0.55, le=0.90)

    # ── Composite score weights ────────────────────────────────────────────────
    weight_technical: float = 0.35
    weight_fundamental: float = 0.30
    weight_news: float = 0.20
    weight_sentiment: float = 0.15

    # ── Data refresh intervals (seconds) ──────────────────────────────────────
    ohlcv_refresh_sec: int = 300        # 5 min
    option_chain_refresh_sec: int = 300
    sentiment_refresh_sec: int = 900    # 15 min
    news_refresh_sec: int = 1800        # 30 min
    fundamentals_refresh_sec: int = 86400  # daily
    fii_dii_refresh_sec: int = 86400

    # ── NSE session windows (IST) ──────────────────────────────────────────────
    market_open_time: str = "09:15"
    market_close_time: str = "15:30"
    mis_squareoff_time: str = "15:10"   # square off MIS before this
    pre_market_start: str = "09:00"

    # ── Technical indicator params ─────────────────────────────────────────────
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    ema_short: int = 20
    ema_mid: int = 50
    ema_long: int = 200
    atr_period: int = 14
    volume_avg_period: int = 20

    # ── F&O analysis params ────────────────────────────────────────────────────
    iv_rank_expensive_threshold: float = 0.80   # skip buying if IV rank > this
    iv_rank_cheap_threshold: float = 0.30       # ideal to buy options below this
    pcr_extreme_bullish: float = 1.50           # contrarian buy signal
    pcr_bullish: float = 1.30
    pcr_bearish: float = 0.70
    pcr_extreme_bearish: float = 0.50           # contrarian sell signal
    option_max_loss_pct: float = 0.50           # exit option at 50% premium loss

    # ── Penny stock filters ────────────────────────────────────────────────────
    penny_min_market_cap_cr: float = 10.0
    penny_max_market_cap_cr: float = 500.0
    penny_min_price: float = 1.0
    penny_max_price: float = 100.0
    penny_min_avg_volume: int = 50_000
    penny_max_promoter_pledging_pct: float = 0.20
    penny_min_promoter_holding_pct: float = 0.30
    penny_max_de_ratio: float = 2.0
    penny_stop_loss_pct: float = 0.15
    penny_target_pct_low: float = 0.25
    penny_target_pct_high: float = 0.50
    penny_volume_spike_threshold: float = 5.0   # operator activity flag

    # ── Multi-strategy consensus engine ───────────────────────────────────────
    strategy_min_votes: int = Field(default=2, ge=1, le=7)
    strategy_min_consensus_confidence: float = Field(default=0.72, ge=0.50, le=0.95)
    auto_scan_enabled: bool = True
    trailing_stop_atr_mult: float = Field(default=1.5, ge=0.5, le=3.0)


settings = Settings()

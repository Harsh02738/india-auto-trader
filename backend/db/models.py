"""SQLAlchemy ORM models."""

from datetime import datetime
from sqlalchemy import Column, DateTime, Float, Integer, String, Boolean, Text
from sqlalchemy.ext.asyncio import AsyncAttrs, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config.settings import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSession = async_sessionmaker(engine, expire_on_commit=False)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    order_id       = Column(String(64), unique=True, nullable=True)
    symbol         = Column(String(32), nullable=False, index=True)
    tier           = Column(String(16), nullable=False)  # EQUITY / FNO / PENNY
    action         = Column(String(8),  nullable=False)  # BUY / SELL
    product        = Column(String(8),  nullable=False)  # MIS / CNC
    qty            = Column(Integer,    nullable=False)
    entry_price    = Column(Float,      nullable=True)
    exit_price     = Column(Float,      nullable=True)
    stop_loss      = Column(Float,      nullable=True)
    target         = Column(Float,      nullable=True)
    realized_pnl   = Column(Float,      nullable=True)
    is_open        = Column(Boolean,    default=True)
    composite_score = Column(Float,     nullable=True)
    confidence     = Column(String(16), nullable=True)
    reasoning      = Column(Text,       nullable=True)
    order_type     = Column(String(16), nullable=True)
    tag            = Column(String(32), default="CLAUDE_AUTO")
    executed_at    = Column(DateTime,   default=datetime.utcnow)
    closed_at      = Column(DateTime,   nullable=True)

    # F&O extras
    option_type    = Column(String(4),  nullable=True)   # CE / PE
    strike         = Column(Float,      nullable=True)
    expiry         = Column(String(16), nullable=True)
    premium_paid   = Column(Float,      nullable=True)


class Signal(Base):
    __tablename__ = "signals"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    symbol          = Column(String(32), nullable=False, index=True)
    tier            = Column(String(16), nullable=False)
    action          = Column(String(8),  nullable=False)
    entry_price     = Column(Float,      nullable=True)
    stop_loss       = Column(Float,      nullable=True)
    target          = Column(Float,      nullable=True)
    composite_score = Column(Float,      nullable=True)
    technical_score = Column(Float,      nullable=True)
    fundamental_score = Column(Float,    nullable=True)
    sentiment_score = Column(Float,      nullable=True)
    news_score      = Column(Float,      nullable=True)
    confidence      = Column(String(16), nullable=True)
    risk_reward     = Column(Float,      nullable=True)
    reasoning       = Column(Text,       nullable=True)
    executed        = Column(Boolean,    default=False)
    created_at      = Column(DateTime,   default=datetime.utcnow)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date     = Column(String(16), nullable=False, index=True)
    account_equity    = Column(Float, nullable=True)
    cash_available    = Column(Float, nullable=True)
    open_positions    = Column(Integer, default=0)
    daily_pnl         = Column(Float,  default=0.0)
    realized_pnl_total = Column(Float, default=0.0)
    circuit_state     = Column(String(16), default="SAFE")
    circuit_reason    = Column(String(256), nullable=True)
    consecutive_losses = Column(Integer, default=0)
    drawdown_pct      = Column(Float,  default=0.0)
    created_at        = Column(DateTime, default=datetime.utcnow)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

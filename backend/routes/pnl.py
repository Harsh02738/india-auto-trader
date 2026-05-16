from fastapi import APIRouter
from sqlalchemy import select, func
from backend.db.models import AsyncSession, Trade, PortfolioSnapshot

router = APIRouter(prefix="/pnl", tags=["pnl"])


@router.get("/summary")
async def pnl_summary():
    async with AsyncSession() as session:
        # Total trades
        total_result = await session.execute(select(func.count(Trade.id)))
        total_trades = total_result.scalar() or 0

        # Realized P&L
        pnl_result = await session.execute(
            select(func.sum(Trade.realized_pnl)).where(Trade.is_open == False)
        )
        realized_pnl = pnl_result.scalar() or 0.0

        # Win count
        wins_result = await session.execute(
            select(func.count(Trade.id)).where(Trade.realized_pnl > 0, Trade.is_open == False)
        )
        wins = wins_result.scalar() or 0

        closed_result = await session.execute(
            select(func.count(Trade.id)).where(Trade.is_open == False)
        )
        closed = closed_result.scalar() or 0

        win_rate = round(wins / closed * 100, 1) if closed > 0 else 0.0

        # Daily P&L history from snapshots
        snap_result = await session.execute(
            select(PortfolioSnapshot.snapshot_date, PortfolioSnapshot.daily_pnl)
            .order_by(PortfolioSnapshot.snapshot_date)
            .limit(90)
        )
        daily_pnl = [{"date": r[0], "pnl": r[1]} for r in snap_result.all()]

        return {
            "total_trades":   total_trades,
            "closed_trades":  closed,
            "open_trades":    total_trades - closed,
            "realized_pnl":   round(realized_pnl, 2),
            "win_rate":       win_rate,
            "wins":           wins,
            "losses":         closed - wins,
            "daily_pnl":      daily_pnl,
        }

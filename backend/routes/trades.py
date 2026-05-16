from fastapi import APIRouter, HTTPException
from sqlalchemy import select, desc
from backend.db.models import AsyncSession, Trade

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("")
async def list_trades(limit: int = 50, open_only: bool = False):
    async with AsyncSession() as session:
        stmt = select(Trade).order_by(desc(Trade.executed_at)).limit(limit)
        if open_only:
            stmt = stmt.where(Trade.is_open == True)
        result = await session.execute(stmt)
        trades = result.scalars().all()
        return [
            {
                "id":           t.id,
                "order_id":     t.order_id,
                "symbol":       t.symbol,
                "tier":         t.tier,
                "action":       t.action,
                "product":      t.product,
                "qty":          t.qty,
                "entry_price":  t.entry_price,
                "exit_price":   t.exit_price,
                "stop_loss":    t.stop_loss,
                "target":       t.target,
                "realized_pnl": t.realized_pnl,
                "is_open":      t.is_open,
                "score":        t.composite_score,
                "executed_at":  str(t.executed_at),
                "closed_at":    str(t.closed_at) if t.closed_at else None,
            }
            for t in trades
        ]


@router.get("/{trade_id}")
async def get_trade(trade_id: int):
    async with AsyncSession() as session:
        trade = await session.get(Trade, trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")
        return trade

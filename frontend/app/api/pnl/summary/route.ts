import { NextResponse } from "next/server";
import { createServerClient } from "@/lib/supabase";

export async function GET() {
  const db = createServerClient();

  // Fetch all closed trades for aggregation
  const { data: closed, error } = await db
    .from("trades")
    .select("realized_pnl, tier, executed_at, closed_at")
    .eq("is_open", false);

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  const trades = closed ?? [];
  const totalTrades  = trades.length;
  const wins         = trades.filter(t => (t.realized_pnl ?? 0) > 0).length;
  const losses       = totalTrades - wins;
  const realizedPnl  = trades.reduce((s, t) => s + (t.realized_pnl ?? 0), 0);
  const winRate      = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;

  // Daily P&L from portfolio snapshots
  const { data: snaps } = await db
    .from("portfolio_snapshots")
    .select("snapshot_date, daily_pnl")
    .order("snapshot_date", { ascending: true })
    .limit(90);

  // Fetch all trades count (including open)
  const { count: allCount } = await db
    .from("trades")
    .select("*", { count: "exact", head: true });

  const openCount = (allCount ?? 0) - totalTrades;

  return NextResponse.json({
    total_trades:   allCount ?? 0,
    closed_trades:  totalTrades,
    open_trades:    openCount,
    realized_pnl:   Math.round(realizedPnl * 100) / 100,
    win_rate:       Math.round(winRate * 10) / 10,
    wins,
    losses,
    daily_pnl: (snaps ?? []).map(s => ({ date: s.snapshot_date, pnl: s.daily_pnl })),
  });
}

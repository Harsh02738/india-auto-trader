// Backend is now FastAPI + SQLite — Supabase replaced.
// This file retains type definitions and provides a fetch proxy helper.

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

/** Proxy helper: Next.js server routes → FastAPI backend. */
export async function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${BACKEND_URL}${path}`, init);
}

export const supabaseConfigured = false;

// ── Type definitions (match local_db SQLite schema) ───────────────────────────

export type Trade = {
  id: number;
  order_id: string | null;
  symbol: string;
  tier: "EQUITY" | "FNO" | "PENNY";
  action: "BUY" | "SELL";
  product: string;
  qty: number;
  entry_price: number | null;
  exit_price: number | null;
  stop_loss: number | null;
  target: number | null;
  realized_pnl: number | null;
  is_open: boolean | number;
  composite_score: number | null;
  confidence: string | null;
  reasoning: string | null;
  tag: string | null;
  executed_at: string;
  closed_at: string | null;
};

export type Signal = {
  id: number;
  symbol: string;
  tier: string;
  action: string;
  entry_price: number | null;
  stop_loss: number | null;
  target: number | null;
  quantity: number | null;
  composite_score: number | null;
  technical_score: number | null;
  fundamental_score: number | null;
  sentiment_score: number | null;
  news_score: number | null;
  confidence: string | null;
  risk_reward: number | null;
  risk_amount_inr: number | null;
  reasoning: string | null;
  executed: boolean | number;
  created_at: string;
};

export type PortfolioSnapshot = {
  id?: number;
  snapshot_date: string;
  account_equity: number | null;
  cash_available: number | null;
  open_positions: number;
  daily_pnl: number;
  realized_pnl_total: number;
  circuit_state: string;
  circuit_reason: string | null;
  consecutive_losses: number;
  drawdown_pct: number;
  created_at?: string;
};

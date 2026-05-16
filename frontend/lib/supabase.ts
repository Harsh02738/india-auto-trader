import { createClient } from "@supabase/supabase-js";

// Support both Vercel integration naming (SUPABASE_URL) and manual naming (NEXT_PUBLIC_SUPABASE_URL)
const url     = process.env.NEXT_PUBLIC_SUPABASE_URL
             ?? process.env.SUPABASE_URL
             ?? "https://placeholder.supabase.co";
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
             ?? process.env.SUPABASE_ANON_KEY
             ?? "placeholder";

// Browser client (used by components for Realtime subscriptions)
export const supabase = createClient(url, anonKey);

// Server client (used in API routes with service-role key for full access)
export function createServerClient() {
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY ?? anonKey;
  return createClient(url, serviceKey, {
    auth: { persistSession: false },
  });
}

export const supabaseConfigured =
  !!process.env.NEXT_PUBLIC_SUPABASE_URL &&
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY !== "placeholder";

// ── Type helpers ──────────────────────────────────────────────
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
  is_open: boolean;
  composite_score: number | null;
  confidence: string | null;
  reasoning: string | null;
  tag: string | null;
  executed_at: string;
  closed_at: string | null;
  option_type: "CE" | "PE" | null;
  strike: number | null;
  expiry: string | null;
  premium_paid: number | null;
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
  executed: boolean;
  created_at: string;
};

export type PortfolioSnapshot = {
  id: number;
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
  created_at: string;
};

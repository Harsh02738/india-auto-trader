-- ============================================================
-- India Auto-Trader — Supabase Schema
-- Run this in the Supabase SQL Editor (Project → SQL Editor → New Query)
-- ============================================================

-- Trades: every order placed via Kotak Neo
create table if not exists trades (
  id              bigserial primary key,
  order_id        text unique,
  symbol          text not null,
  tier            text not null check (tier in ('EQUITY', 'FNO', 'PENNY')),
  action          text not null check (action in ('BUY', 'SELL')),
  product         text not null check (product in ('MIS', 'CNC', 'NRML')),
  qty             integer not null,
  entry_price     numeric(12, 4),
  exit_price      numeric(12, 4),
  stop_loss       numeric(12, 4),
  target          numeric(12, 4),
  realized_pnl    numeric(12, 2),
  is_open         boolean default true,
  composite_score numeric(5, 4),
  confidence      text,
  reasoning       text,
  order_type      text,
  tag             text default 'CLAUDE_AUTO',
  executed_at     timestamptz default now(),
  closed_at       timestamptz,
  -- F&O extras
  option_type     text check (option_type in ('CE', 'PE', null)),
  strike          numeric(12, 2),
  expiry          text,
  premium_paid    numeric(12, 4)
);

-- Signals: analysis output written by Claude Code skills
create table if not exists signals (
  id                  bigserial primary key,
  symbol              text not null,
  tier                text not null,
  action              text not null,
  entry_price         numeric(12, 4),
  stop_loss           numeric(12, 4),
  target              numeric(12, 4),
  quantity            integer,
  composite_score     numeric(5, 4),
  technical_score     numeric(5, 4),
  fundamental_score   numeric(5, 4),
  sentiment_score     numeric(5, 4),
  news_score          numeric(5, 4),
  confidence          text,
  risk_reward         numeric(6, 3),
  risk_amount_inr     numeric(12, 2),
  reasoning           text,
  earnings_within_days integer,
  option_pcr          numeric(6, 3),
  executed            boolean default false,
  created_at          timestamptz default now()
);

-- Portfolio snapshots: one row per trading day (EOD state)
create table if not exists portfolio_snapshots (
  id                  bigserial primary key,
  snapshot_date       text not null,
  account_equity      numeric(14, 2),
  cash_available      numeric(14, 2),
  open_positions      integer default 0,
  daily_pnl           numeric(12, 2) default 0,
  realized_pnl_total  numeric(14, 2) default 0,
  circuit_state       text default 'SAFE',
  circuit_reason      text,
  consecutive_losses  integer default 0,
  drawdown_pct        numeric(6, 4) default 0,
  created_at          timestamptz default now(),
  unique (snapshot_date)
);

-- ── Indexes ──────────────────────────────────────────────────
create index if not exists trades_symbol_idx    on trades (symbol);
create index if not exists trades_is_open_idx   on trades (is_open);
create index if not exists trades_executed_idx  on trades (executed_at desc);
create index if not exists signals_symbol_idx   on signals (symbol);
create index if not exists signals_created_idx  on signals (created_at desc);
create index if not exists signals_executed_idx on signals (executed);

-- ── Enable Realtime on trades + signals ──────────────────────
-- (Run these separately in Supabase Dashboard → Database → Replication
--  or via the Realtime section. Shown here as reference.)
-- alter publication supabase_realtime add table trades;
-- alter publication supabase_realtime add table signals;

-- ── Row Level Security (optional — disable for server-side use) ──
-- alter table trades enable row level security;
-- alter table signals enable row level security;
-- alter table portfolio_snapshots enable row level security;

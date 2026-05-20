-- ============================================================
-- India Auto-Trader — Trade Journal Migration
-- Adds the trade_journal table for memory engineering:
--   Auto-logged trade outcomes enable EV/Kelly/RoR computation
--   and allow the AI to review its own historical decisions.
--
-- Run in Supabase SQL Editor: Project → SQL Editor → New Query
-- ============================================================

create table if not exists trade_journal (
  id                    bigserial primary key,

  -- Link to the trades table
  trade_order_id        text references trades(order_id) on delete set null,

  -- What was traded
  symbol                text not null,
  tier                  text not null check (tier in ('EQUITY', 'FNO', 'PENNY')),

  -- Entry conditions (snapshot of the consensus at trade time)
  strategy_votes        text,          -- comma-separated strategy names that voted e.g. "MACD_RSI,VWAP,Composite4F"
  vote_count            integer,
  entry_conditions_json jsonb,         -- full ConsensusSignal JSON for deep review
  tradingview_action    text,          -- TV confluence action at time of trade
  tradingview_score     numeric(5, 3), -- TV confluence score 0-1

  -- Prices
  entry_price           numeric(12, 4),
  stop_loss             numeric(12, 4),
  target                numeric(12, 4),
  exit_price            numeric(12, 4),

  -- Math metrics at entry (from TradingMathEngine)
  ev_at_entry           numeric(8, 6),      -- expected value per trade fraction
  kelly_at_entry        numeric(8, 6),      -- half-Kelly fraction used
  ror_at_entry          numeric(8, 6),      -- risk of ruin at time of entry
  ror_status_at_entry   text,               -- SAFE / CAUTION / DANGER / HALT

  -- Outcome
  final_pnl_pct         numeric(10, 6),     -- % P&L: (exit - entry) / entry
  final_pnl_inr         numeric(12, 2),     -- absolute P&L in INR
  outcome               text check (outcome in ('WIN', 'LOSS', 'BREAKEVEN', null)),
  hold_duration_hours   numeric(8, 2),      -- how long the trade was held

  -- Context and learning
  market_context        text,               -- brief note on market conditions at entry
  lessons_text          text,               -- what worked / what didn't
  tv_matched_direction  boolean,            -- did TV confluence agree with the trade?
  circuit_breaker_state text default 'SAFE',

  -- Timestamps
  created_at            timestamptz default now(),
  closed_at             timestamptz
);

-- ── Indexes ──────────────────────────────────────────────────────────────────
create index if not exists tj_symbol_idx    on trade_journal (symbol);
create index if not exists tj_outcome_idx   on trade_journal (outcome);
create index if not exists tj_created_idx   on trade_journal (created_at desc);
create index if not exists tj_strategy_idx  on trade_journal (strategy_votes);
create index if not exists tj_order_idx     on trade_journal (trade_order_id);

-- ── Enable Realtime (optional) ───────────────────────────────────────────────
-- alter publication supabase_realtime add table trade_journal;

-- ── Add convenience functions ────────────────────────────────────────────────

-- View: per-strategy win rates (called by get_strategy_performance_stats MCP tool)
create or replace view strategy_performance as
select
  unnest(string_to_array(strategy_votes, ',')) as strategy_name,
  count(*)                                      as total_trades,
  count(*) filter (where outcome = 'WIN')       as wins,
  count(*) filter (where outcome = 'LOSS')      as losses,
  round(avg(case when outcome = 'WIN'  then final_pnl_pct end)::numeric, 6) as avg_win_pct,
  round(abs(avg(case when outcome = 'LOSS' then final_pnl_pct end))::numeric, 6) as avg_loss_pct,
  round(sum(final_pnl_pct)::numeric, 6)         as total_pnl_pct,
  round(avg(final_pnl_pct)::numeric, 6)         as mean_pnl_pct,
  round(avg(ev_at_entry)::numeric, 6)           as avg_ev_at_entry
from trade_journal
where outcome is not null
group by 1
order by total_pnl_pct desc;

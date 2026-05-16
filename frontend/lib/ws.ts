"use client";

import { useEffect, useState } from "react";
import { supabase, supabaseConfigured } from "@/lib/supabase";
import type { Signal, Trade, PortfolioSnapshot } from "@/lib/supabase";

export type LiveState = {
  connected: boolean;
  latestSignals: Signal[];
  latestTrades: Trade[];
  snapshot: PortfolioSnapshot | null;
};

export function useRealtimeTrading(): LiveState {
  const [connected, setConnected] = useState(false);
  const [latestSignals, setLatestSignals] = useState<Signal[]>([]);
  const [latestTrades, setLatestTrades]   = useState<Trade[]>([]);
  const [snapshot, setSnapshot]           = useState<PortfolioSnapshot | null>(null);

  useEffect(() => {
    if (!supabaseConfigured) return; // skip realtime if env vars not set

    // Subscribe to new signals
    const signalChannel = supabase
      .channel("signals-live")
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "signals" },
        (payload) => {
          setLatestSignals(prev => [payload.new as Signal, ...prev].slice(0, 20));
        }
      )
      .subscribe((status) => setConnected(status === "SUBSCRIBED"));

    // Subscribe to trade inserts/updates (position changes)
    const tradeChannel = supabase
      .channel("trades-live")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "trades" },
        (payload) => {
          const updated = payload.new as Trade;
          setLatestTrades(prev => {
            const filtered = prev.filter(t => t.id !== updated.id);
            return [updated, ...filtered].slice(0, 50);
          });
        }
      )
      .subscribe();

    // Subscribe to portfolio snapshot updates
    const snapChannel = supabase
      .channel("snapshot-live")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "portfolio_snapshots" },
        (payload) => setSnapshot(payload.new as PortfolioSnapshot)
      )
      .subscribe();

    return () => {
      supabase.removeChannel(signalChannel);
      supabase.removeChannel(tradeChannel);
      supabase.removeChannel(snapChannel);
    };
  }, []);

  return { connected, latestSignals, latestTrades, snapshot };
}

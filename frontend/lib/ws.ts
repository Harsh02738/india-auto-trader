"use client";

import { useEffect, useRef, useState } from "react";
import { supabase, supabaseConfigured } from "@/lib/supabase";
import type { Signal, Trade, PortfolioSnapshot } from "@/lib/supabase";

export type LiveState = {
  connected: boolean;
  latestSignals: Signal[];
  latestTrades: Trade[];
  snapshot: PortfolioSnapshot | null;
};

export function useRealtimeTrading(): LiveState {
  const [connected, setConnected]       = useState(false);
  const [latestSignals, setLatestSignals] = useState<Signal[]>([]);
  const [latestTrades, setLatestTrades]   = useState<Trade[]>([]);
  const [snapshot, setSnapshot]           = useState<PortfolioSnapshot | null>(null);

  // Unique suffix per component mount so channel names never collide across re-renders
  const suffix = useRef(`${Date.now()}`);

  useEffect(() => {
    if (!supabaseConfigured) return;

    const id = suffix.current;

    const signalCh = supabase
      .channel(`signals-${id}`)
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "signals" }, (p) => {
        setLatestSignals((prev) => [p.new as Signal, ...prev].slice(0, 20));
      })
      .subscribe((status) => setConnected(status === "SUBSCRIBED"));

    const tradeCh = supabase
      .channel(`trades-${id}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "trades" }, (p) => {
        const t = p.new as Trade;
        setLatestTrades((prev) => [t, ...prev.filter((x) => x.id !== t.id)].slice(0, 50));
      })
      .subscribe();

    const snapCh = supabase
      .channel(`snapshot-${id}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "portfolio_snapshots" }, (p) => {
        setSnapshot(p.new as PortfolioSnapshot);
      })
      .subscribe();

    return () => {
      supabase.removeChannel(signalCh);
      supabase.removeChannel(tradeCh);
      supabase.removeChannel(snapCh);
    };
  }, []);

  return { connected, latestSignals, latestTrades, snapshot };
}

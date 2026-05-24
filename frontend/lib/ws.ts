"use client";

import { useEffect, useState } from "react";
import type { Signal, Trade, PortfolioSnapshot } from "@/lib/supabase";

export type LiveState = {
  connected: boolean;
  latestSignals: Signal[];
  latestTrades: Trade[];
  snapshot: PortfolioSnapshot | null;
};

const WS_URL =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws")
    : "";

export function useRealtimeTrading(): LiveState {
  const [connected, setConnected]         = useState(false);
  const [latestSignals, setLatestSignals] = useState<Signal[]>([]);
  const [latestTrades, setLatestTrades]   = useState<Trade[]>([]);
  const [snapshot, setSnapshot]           = useState<PortfolioSnapshot | null>(null);

  useEffect(() => {
    if (!WS_URL) return;

    let ws: WebSocket;
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let pingInterval: ReturnType<typeof setInterval>;

    const connect = () => {
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        setConnected(true);
        pingInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 30_000);
      };

      ws.onclose = () => {
        setConnected(false);
        clearInterval(pingInterval);
        reconnectTimer = setTimeout(connect, 3_000);
      };

      ws.onerror = () => ws.close();

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === "tick") {
            if (msg.top_signals?.length) setLatestSignals(msg.top_signals);
            if (msg.open_trades)         setLatestTrades(msg.open_trades);
            if (msg.snapshot && Object.keys(msg.snapshot).length)
              setSnapshot(msg.snapshot as PortfolioSnapshot);
          } else if (msg.type === "trade_executed") {
            const t = msg.data as Trade;
            setLatestTrades((prev) =>
              [t, ...prev.filter((x) => x.id !== t.id)].slice(0, 50)
            );
          } else if (msg.type === "signal") {
            const s = msg.data as Signal;
            setLatestSignals((prev) => [s, ...prev].slice(0, 20));
          } else if (msg.type === "trade_closed") {
            const t = msg.data as Trade;
            setLatestTrades((prev) => prev.map((x) => (x.id === t.id ? t : x)));
          }
        } catch {}
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      clearInterval(pingInterval!);
      ws?.close();
    };
  }, []);

  return { connected, latestSignals, latestTrades, snapshot };
}

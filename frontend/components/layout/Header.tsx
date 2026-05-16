"use client";

import dynamic from "next/dynamic";
import { Wifi, WifiOff } from "lucide-react";
import { useRealtimeTrading } from "@/lib/ws";
import { circuitColor } from "@/lib/utils";

const LiveTicker = dynamic(() => import("./LiveTicker"), { ssr: false });

export default function Header() {
  const { connected, latestSignals, snapshot } = useRealtimeTrading();
  const state = snapshot?.circuit_state ?? "SAFE";

  return (
    <header className="h-12 bg-surface border-b border-border flex items-center px-4 gap-4 shrink-0">
      <div className="flex-1 overflow-hidden">
        <LiveTicker signals={latestSignals} />
      </div>

      <div className="flex items-center gap-4 shrink-0">
        <span className={`text-xs font-mono font-semibold ${circuitColor(state)}`}>
          ● {state}
        </span>
        <span className={`flex items-center gap-1 text-xs ${connected ? "text-bull" : "text-subtle"}`}>
          {connected ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
          {connected ? "LIVE" : "OFFLINE"}
        </span>
      </div>
    </header>
  );
}

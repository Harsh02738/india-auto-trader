"use client";

import { useWebSocket } from "@/lib/ws";
import { circuitColor } from "@/lib/utils";
import { Wifi, WifiOff } from "lucide-react";
import LiveTicker from "./LiveTicker";

export default function Header() {
  const { connected, lastMessage } = useWebSocket();
  const state = lastMessage?.snapshot?.circuit_state ?? "SAFE";

  return (
    <header className="h-12 bg-surface border-b border-border flex items-center px-4 gap-4 shrink-0">
      {/* Live ticker */}
      <div className="flex-1 overflow-hidden">
        <LiveTicker signals={lastMessage?.top_signals ?? []} />
      </div>

      {/* Right side */}
      <div className="flex items-center gap-4 shrink-0">
        {/* Circuit state */}
        <span className={`text-xs font-mono font-semibold ${circuitColor(state)}`}>
          ● {state}
        </span>

        {/* WS indicator */}
        <span className={`flex items-center gap-1 text-xs ${connected ? "text-bull" : "text-subtle"}`}>
          {connected ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
          {connected ? "LIVE" : "OFFLINE"}
        </span>
      </div>
    </header>
  );
}

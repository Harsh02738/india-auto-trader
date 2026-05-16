"use client";

import dynamic from "next/dynamic";
import { Wifi, WifiOff, Menu } from "lucide-react";
import { useRealtimeTrading } from "@/lib/ws";
import { circuitColor } from "@/lib/utils";
import { useSidebar } from "./Providers";

const LiveTicker = dynamic(() => import("./LiveTicker"), { ssr: false });

export default function Header() {
  const { connected, latestSignals, snapshot } = useRealtimeTrading();
  const { toggle } = useSidebar();
  const state = snapshot?.circuit_state ?? "SAFE";

  return (
    <header className="h-12 bg-surface border-b border-border flex items-center px-3 gap-3 shrink-0">
      {/* Hamburger — mobile only */}
      <button
        onClick={toggle}
        className="md:hidden text-subtle hover:text-text p-1 shrink-0"
        aria-label="Open menu"
      >
        <Menu className="w-5 h-5" />
      </button>

      {/* Live ticker */}
      <div className="flex-1 overflow-hidden min-w-0">
        <LiveTicker signals={latestSignals} />
      </div>

      {/* Status pills */}
      <div className="flex items-center gap-2 shrink-0">
        <span className={`text-xs font-mono font-semibold ${circuitColor(state)} hidden sm:inline`}>
          ● {state}
        </span>
        <span className={`flex items-center gap-1 text-xs ${connected ? "text-bull" : "text-subtle"}`}>
          {connected ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
          <span className="hidden sm:inline">{connected ? "LIVE" : "OFFLINE"}</span>
        </span>
      </div>
    </header>
  );
}

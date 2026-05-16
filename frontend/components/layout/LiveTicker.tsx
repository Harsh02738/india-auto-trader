"use client";

import { cn } from "@/lib/utils";

interface TickerItem {
  symbol: string;
  action?: string | null;
  composite_score?: number | null;
  entry_price?: number | null;
}

export default function LiveTicker({ signals }: { signals: TickerItem[] }) {
  if (!signals.length) return <span className="text-xs text-subtle font-mono">No signals yet</span>;

  // Duplicate for seamless loop
  const items = [...signals, ...signals];

  return (
    <div className="overflow-hidden h-full flex items-center">
      <div className="ticker-track animate flex gap-8">
        {items.map((s, i) => (
          <span key={i} className="flex items-center gap-1.5 text-xs font-mono shrink-0">
            <span className="text-subtle">{s.symbol}</span>
            {s.action && (
              <span className={cn(
                "px-1 rounded text-[10px] font-semibold",
                s.action === "BUY" ? "text-bull" : "text-bear"
              )}>
                {s.action}
              </span>
            )}
            {s.composite_score && (
              <span className="text-subtle">{(s.composite_score * 100).toFixed(0)}</span>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

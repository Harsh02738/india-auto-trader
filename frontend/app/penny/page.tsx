"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchPennyCandidates } from "@/lib/api";
import { fmt, scoreColor } from "@/lib/utils";
import { AlertTriangle, CheckCircle2, TrendingUp } from "lucide-react";

export default function PennyPage() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["penny"],
    queryFn: fetchPennyCandidates,
    refetchInterval: 1_800_000, // 30 min
  });

  const candidates: any[] = data?.candidates ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-gold" /> Penny Stock Scanner
          </h1>
          <p className="text-xs text-warn mt-0.5 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            HIGH RISK — Max 1% portfolio per position. LIMIT orders only.
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="px-3 py-1.5 bg-accent/20 text-accent rounded text-xs font-medium hover:bg-accent/30 transition-colors"
        >
          Re-scan
        </button>
      </div>

      {/* Stats bar */}
      {data && (
        <div className="flex gap-6 text-sm">
          <span className="text-subtle">Scanned: <strong className="text-text">{data.scanned_count}</strong></span>
          <span className="text-subtle">Passed: <strong className="text-bull">{data.passed_count}</strong></span>
          <span className="text-subtle">Rejected: <strong className="text-bear">{data.rejected_count}</strong></span>
          <span className="text-xs text-subtle">Last: {data.timestamp ? new Date(data.timestamp).toLocaleString("en-IN") : "—"}</span>
        </div>
      )}

      {isLoading ? (
        <p className="text-subtle text-center py-12">Scanning NSE SME platform...</p>
      ) : candidates.length === 0 ? (
        <p className="text-subtle text-center py-12">No candidates passed all filters.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {candidates.map((c: any) => (
            <div
              key={c.symbol}
              className="bg-surface border border-border rounded-xl p-4 space-y-3 hover:border-gold/30 transition-colors"
            >
              {/* Header */}
              <div className="flex items-center justify-between">
                <span className="font-mono font-bold text-gold text-lg">
                  {c.symbol}
                </span>
                <span className={`text-sm font-mono font-bold ${scoreColor(c.score)}`}>
                  {(c.score * 100).toFixed(0)}
                </span>
              </div>

              {/* Price */}
              <div className="flex items-center justify-between text-sm">
                <span className="font-mono font-semibold">₹{c.price}</span>
                <span className={`text-xs ${c.price_5d_chg_pct >= 0 ? "text-bull" : "text-bear"}`}>
                  {c.price_5d_chg_pct >= 0 ? "+" : ""}{c.price_5d_chg_pct}% 5d
                </span>
              </div>

              {/* Filters */}
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <div className="flex justify-between">
                  <span className="text-subtle">Mkt Cap</span>
                  <span>₹{c.market_cap_cr}Cr</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-subtle">Promoter</span>
                  <span className={c.promoter_pct >= 30 ? "text-bull" : "text-bear"}>
                    {c.promoter_pct}%
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-subtle">D/E</span>
                  <span className={c.de_ratio <= 1.5 ? "text-bull" : "text-warn"}>
                    {fmt(c.de_ratio, 2)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-subtle">Avg Vol</span>
                  <span>{(c.avg_volume_20d / 1000).toFixed(0)}K</span>
                </div>
              </div>

              {/* SL / Target */}
              <div className="flex gap-3 text-xs font-mono">
                <span className="text-bear">SL: ₹{c.stop_loss}</span>
                <span className="text-bull">T1: ₹{c.target_low}</span>
                <span className="text-bull">T2: ₹{c.target_high}</span>
              </div>

              {/* Flags */}
              {c.flags?.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {c.flags.map((f: string) => (
                    <span key={f} className="px-1.5 py-0.5 bg-warn/10 text-warn rounded text-[10px]">
                      {f}
                    </span>
                  ))}
                </div>
              )}

              {/* Promoter buying */}
              {c.promoter_buying && (
                <div className="flex items-center gap-1 text-bull text-xs">
                  <CheckCircle2 className="w-3 h-3" />
                  Promoter buying detected
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

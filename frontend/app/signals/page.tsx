"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchSignals } from "@/lib/api";
import { fmt, fmtPct, scoreColor, signalBadge } from "@/lib/utils";
import { useState } from "react";

const TIERS = ["ALL", "EQUITY", "FNO", "PENNY"];

export default function SignalsPage() {
  const [tier, setTier] = useState("ALL");
  const { data: signals = [], isLoading } = useQuery({
    queryKey: ["signals"],
    queryFn: fetchSignals,
    refetchInterval: 30_000,
  });

  const filtered = tier === "ALL" ? signals : signals.filter((s: any) => s.tier === tier);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Signals</h1>
        <div className="flex gap-1">
          {TIERS.map(t => (
            <button
              key={t}
              onClick={() => setTier(t)}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                tier === t
                  ? "bg-accent text-white"
                  : "bg-surface text-subtle hover:text-text border border-border"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div className="bg-surface border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <p className="text-center text-subtle py-12">Loading signals...</p>
        ) : filtered.length === 0 ? (
          <p className="text-center text-subtle py-12">No signals found. Run /scan-watchlist in Claude Code.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th><th>Tier</th><th>Action</th>
                <th>Entry</th><th>SL</th><th>Target</th>
                <th>Score</th><th>Tech</th><th>Fund</th>
                <th>R:R</th><th>Confidence</th><th>Executed</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s: any) => (
                <tr key={s.symbol + s.timestamp}>
                  <td className="font-mono font-semibold text-accent">{s.symbol}</td>
                  <td className="text-xs text-subtle">{s.tier}</td>
                  <td>
                    <span className={`px-2 py-0.5 rounded text-xs font-bold ${signalBadge(s.action)}`}>
                      {s.action}
                    </span>
                  </td>
                  <td className="font-mono">₹{fmt(s.entry_price)}</td>
                  <td className="font-mono text-bear">₹{fmt(s.stop_loss)}</td>
                  <td className="font-mono text-bull">₹{fmt(s.target)}</td>
                  <td className={`font-mono font-semibold ${scoreColor(s.composite_score)}`}>
                    {(s.composite_score * 100).toFixed(0)}
                  </td>
                  <td className="font-mono text-subtle">{(s.technical_score * 100).toFixed(0)}</td>
                  <td className="font-mono text-subtle">{(s.fundamental_score * 100).toFixed(0)}</td>
                  <td className="font-mono">{fmt(s.risk_reward)}</td>
                  <td className="text-xs text-subtle">{s.confidence}</td>
                  <td>
                    {s.executed
                      ? <span className="text-bull text-xs">✓</span>
                      : <span className="text-subtle text-xs">—</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

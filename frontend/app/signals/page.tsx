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
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h1 className="text-xl font-semibold">Signals</h1>
        <div className="flex gap-1 flex-wrap">
          {TIERS.map(t => (
            <button
              key={t}
              onClick={() => setTier(t)}
              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
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
          <p className="text-center text-subtle py-12 text-sm px-4">
            No signals found. Run /scan-watchlist in Claude Code.
          </p>
        ) : (
          <>
            {/* Mobile card list */}
            <div className="md:hidden divide-y divide-border">
              {filtered.map((s: any) => (
                <div key={s.symbol + s.created_at} className="p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-bold text-accent text-base">{s.symbol}</span>
                    <span className={`px-2 py-0.5 rounded text-xs font-bold ${signalBadge(s.action)}`}>
                      {s.action}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <p className="text-subtle">Score</p>
                      <p className={`font-mono font-bold ${scoreColor(s.composite_score)}`}>
                        {(s.composite_score * 100).toFixed(0)}
                      </p>
                    </div>
                    <div>
                      <p className="text-subtle">Entry</p>
                      <p className="font-mono">₹{fmt(s.entry_price)}</p>
                    </div>
                    <div>
                      <p className="text-subtle">R:R</p>
                      <p className="font-mono">{fmt(s.risk_reward)}</p>
                    </div>
                    <div>
                      <p className="text-subtle">SL</p>
                      <p className="font-mono text-bear">₹{fmt(s.stop_loss)}</p>
                    </div>
                    <div>
                      <p className="text-subtle">Target</p>
                      <p className="font-mono text-bull">₹{fmt(s.target)}</p>
                    </div>
                    <div>
                      <p className="text-subtle">Conf.</p>
                      <p className="text-text">{s.confidence}</p>
                    </div>
                  </div>
                  {s.reasoning && (
                    <p className="text-xs text-subtle leading-relaxed">{s.reasoning}</p>
                  )}
                </div>
              ))}
            </div>

            {/* Desktop table */}
            <div className="hidden md:block overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Symbol</th><th>Tier</th><th>Action</th>
                    <th>Entry</th><th>SL</th><th>Target</th>
                    <th>Score</th><th>Tech</th><th>Fund</th>
                    <th>R:R</th><th>Confidence</th><th>Done</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((s: any) => (
                    <tr key={s.symbol + s.created_at}>
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
            </div>
          </>
        )}
      </div>
    </div>
  );
}

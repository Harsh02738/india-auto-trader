"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchOptionChain, fetchMarketPcr } from "@/lib/api";
import { fmt } from "@/lib/utils";
import { useState } from "react";

const INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY"];

function PcrGauge({ pcr }: { pcr: number }) {
  const color = pcr >= 1.3 ? "text-bull" : pcr <= 0.7 ? "text-bear" : "text-warn";
  const label = pcr >= 1.5 ? "EXTREME FEAR" : pcr >= 1.3 ? "BEARISH SENTIMENT"
    : pcr <= 0.5 ? "EXTREME GREED" : pcr <= 0.7 ? "BULLISH SENTIMENT" : "NEUTRAL";
  return (
    <div className="flex items-center gap-3">
      <span className={`text-3xl font-mono font-bold ${color}`}>{fmt(pcr, 2)}</span>
      <span className={`text-xs font-semibold ${color}`}>{label}</span>
    </div>
  );
}

export default function OptionsPage() {
  const [symbol, setSymbol] = useState("NIFTY");
  const { data: pcr }   = useQuery({ queryKey: ["pcr"], queryFn: fetchMarketPcr, refetchInterval: 60_000 });
  const { data: chain } = useQuery({
    queryKey: ["chain", symbol],
    queryFn: () => fetchOptionChain(symbol),
    refetchInterval: 60_000,
  });

  const chainRows: any[] = chain?.chain ?? [];
  const atm = chain?.atm_strike ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">F&O — Option Chain</h1>
        <div className="flex gap-1">
          {INDEX_SYMBOLS.map(s => (
            <button
              key={s}
              onClick={() => setSymbol(s)}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                symbol === s
                  ? "bg-accent text-white"
                  : "bg-surface text-subtle hover:text-text border border-border"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-xs text-subtle mb-1">Put-Call Ratio</p>
          <PcrGauge pcr={chain?.pcr ?? pcr?.indices?.[symbol]?.pcr ?? 0} />
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-xs text-subtle mb-1">Max Pain</p>
          <p className="text-2xl font-mono font-bold">
            ₹{chain?.max_pain_strike?.toLocaleString("en-IN") ?? "—"}
          </p>
          <p className="text-xs text-subtle">{chain?.max_pain_diff_pct ?? 0}% from spot</p>
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-xs text-subtle mb-1">Underlying</p>
          <p className="text-2xl font-mono font-bold">
            ₹{chain?.underlying_value?.toLocaleString("en-IN") ?? "—"}
          </p>
          <p className="text-xs text-subtle">ATM: {atm.toLocaleString("en-IN")}</p>
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-xs text-subtle mb-1">Straddle Price</p>
          <p className="text-2xl font-mono font-bold">₹{fmt(chain?.straddle_price)}</p>
          <p className="text-xs text-subtle">ATM CE + PE</p>
        </div>
      </div>

      {/* Support / Resistance */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-surface border border-border rounded-xl p-4">
          <h3 className="text-sm font-semibold text-bear mb-2">Resistance (Call OI)</h3>
          <div className="space-y-1">
            {(chain?.resistance_strikes ?? []).map((s: number) => (
              <div key={s} className="flex justify-between text-sm font-mono">
                <span>{s.toLocaleString("en-IN")}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <h3 className="text-sm font-semibold text-bull mb-2">Support (Put OI)</h3>
          <div className="space-y-1">
            {(chain?.support_strikes ?? []).map((s: number) => (
              <div key={s} className="flex justify-between text-sm font-mono">
                <span>{s.toLocaleString("en-IN")}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Option chain table */}
      <div className="bg-surface border border-border rounded-xl overflow-auto">
        <table className="data-table text-xs">
          <thead>
            <tr>
              <th className="text-bull">CE OI</th>
              <th className="text-bull">CE Vol</th>
              <th className="text-bull">CE IV%</th>
              <th className="text-bull">CE LTP</th>
              <th className="text-center bg-muted/20">Strike</th>
              <th className="text-bear">PE LTP</th>
              <th className="text-bear">PE IV%</th>
              <th className="text-bear">PE Vol</th>
              <th className="text-bear">PE OI</th>
              <th>PCR</th>
            </tr>
          </thead>
          <tbody>
            {chainRows.slice(0, 40).map((row: any) => {
              const isAtm = Math.abs(row.strike - atm) < 50;
              return (
                <tr key={row.strike + row.expiry} className={isAtm ? "bg-accent/10" : ""}>
                  <td className="font-mono text-bull">{(row.ce_oi / 1000).toFixed(0)}K</td>
                  <td className="font-mono text-subtle">{(row.ce_vol / 1000).toFixed(0)}K</td>
                  <td className="font-mono text-subtle">{row.ce_iv}%</td>
                  <td className="font-mono font-medium text-bull">₹{row.ce_ltp}</td>
                  <td className={`text-center font-mono font-bold ${isAtm ? "text-accent" : "text-text"}`}>
                    {row.strike.toLocaleString("en-IN")}
                    {isAtm && <span className="ml-1 text-[9px] text-accent">ATM</span>}
                  </td>
                  <td className="font-mono font-medium text-bear">₹{row.pe_ltp}</td>
                  <td className="font-mono text-subtle">{row.pe_iv}%</td>
                  <td className="font-mono text-subtle">{(row.pe_vol / 1000).toFixed(0)}K</td>
                  <td className="font-mono text-bear">{(row.pe_oi / 1000).toFixed(0)}K</td>
                  <td className="font-mono text-subtle">{row.pcr ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchSnapshot, fetchSignals, fetchFiiDii, fetchPnlSummary } from "@/lib/api";
import { useWebSocket } from "@/lib/ws";
import { fmt, fmtCr, fmtPct, circuitColor, scoreColor, signalBadge } from "@/lib/utils";
import { TrendingUp, TrendingDown, Shield, DollarSign, Activity, Zap } from "lucide-react";

function StatCard({
  label, value, sub, icon: Icon, color = "text-text"
}: {
  label: string; value: string; sub?: string;
  icon: React.ElementType; color?: string;
}) {
  return (
    <div className="bg-surface border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-subtle uppercase tracking-wide">{label}</span>
        <Icon className={`w-4 h-4 ${color}`} />
      </div>
      <p className={`text-2xl font-mono font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-subtle mt-1">{sub}</p>}
    </div>
  );
}

export default function DashboardPage() {
  const { lastMessage } = useWebSocket();
  const { data: snapshot } = useQuery({ queryKey: ["snapshot"], queryFn: fetchSnapshot, refetchInterval: 10_000 });
  const { data: signals }  = useQuery({ queryKey: ["signals"],  queryFn: fetchSignals,  refetchInterval: 30_000 });
  const { data: fii }      = useQuery({ queryKey: ["fii"],      queryFn: fetchFiiDii,   refetchInterval: 60_000 });
  const { data: pnl }      = useQuery({ queryKey: ["pnl"],      queryFn: fetchPnlSummary });

  const snap    = lastMessage?.snapshot ?? snapshot ?? {};
  const topSigs = (signals ?? []).slice(0, 8);
  const circuitState = snap.circuit_state ?? "SAFE";
  const fiiData = lastMessage?.fii_dii ?? fii ?? {};

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <span className="text-xs text-subtle font-mono">NSE/BSE • {new Date().toLocaleTimeString("en-IN")}</span>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Account Equity"
          value={fmtCr(snap.account_equity)}
          sub={`Peak: ${fmtCr(snap.peak_equity)}`}
          icon={DollarSign}
          color="text-text"
        />
        <StatCard
          label="Daily P&L"
          value={fmtCr(snap.daily_pnl)}
          sub={fmtPct(snap.daily_pnl_pct * 100, true)}
          icon={snap.daily_pnl >= 0 ? TrendingUp : TrendingDown}
          color={snap.daily_pnl >= 0 ? "text-bull" : "text-bear"}
        />
        <StatCard
          label="Circuit"
          value={circuitState}
          sub={snap.circuit_reason ?? `${snap.consecutive_losses ?? 0} consec losses`}
          icon={Shield}
          color={circuitColor(circuitState)}
        />
        <StatCard
          label="Win Rate"
          value={`${pnl?.win_rate ?? 0}%`}
          sub={`${pnl?.wins ?? 0}W / ${pnl?.losses ?? 0}L`}
          icon={Activity}
          color="text-accent"
        />
      </div>

      {/* Two columns: signals + FII/DII */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Top signals */}
        <div className="lg:col-span-2 bg-surface border border-border rounded-xl p-4">
          <h2 className="text-sm font-semibold mb-3">Top Signals</h2>
          {topSigs.length === 0 ? (
            <p className="text-subtle text-sm text-center py-8">No signals — run /scan-watchlist</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Tier</th><th>Action</th>
                  <th>Entry</th><th>Score</th><th>R:R</th>
                </tr>
              </thead>
              <tbody>
                {topSigs.map((s: any) => (
                  <tr key={s.symbol}>
                    <td className="font-mono font-medium">{s.symbol}</td>
                    <td className="text-subtle text-xs">{s.tier}</td>
                    <td>
                      <span className={`px-2 py-0.5 rounded text-xs font-semibold ${signalBadge(s.action)}`}>
                        {s.action}
                      </span>
                    </td>
                    <td className="font-mono">₹{fmt(s.entry_price)}</td>
                    <td className={`font-mono ${scoreColor(s.composite_score)}`}>
                      {fmt(s.composite_score * 100, 0)}
                    </td>
                    <td className="text-subtle">{fmt(s.risk_reward)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* FII/DII + positions */}
        <div className="space-y-4">
          {/* FII/DII */}
          <div className="bg-surface border border-border rounded-xl p-4">
            <h2 className="text-sm font-semibold mb-3">FII / DII Flow</h2>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-subtle">FII Net Today</span>
                <span className={`font-mono font-semibold ${(fiiData.fii_net ?? 0) >= 0 ? "text-bull" : "text-bear"}`}>
                  {fmtCr(fiiData.fii_net)}
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-subtle">DII Net Today</span>
                <span className={`font-mono font-semibold ${(fiiData.dii_net ?? 0) >= 0 ? "text-bull" : "text-bear"}`}>
                  {fmtCr(fiiData.dii_net)}
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-subtle">Signal</span>
                <span className="font-mono text-accent text-xs">{fiiData.signal ?? "—"}</span>
              </div>
            </div>
          </div>

          {/* Open positions summary */}
          <div className="bg-surface border border-border rounded-xl p-4">
            <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <Zap className="w-4 h-4 text-warn" /> Open Positions
            </h2>
            <p className="text-3xl font-mono font-bold text-text">
              {snap.open_positions ?? 0}
            </p>
            <p className="text-xs text-subtle mt-1">
              Drawdown: {fmt(snap.drawdown_from_peak_pct * 100, 1)}%
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

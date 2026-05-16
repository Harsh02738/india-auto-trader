"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchPnlSummary, fetchTrades } from "@/lib/api";
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid
} from "recharts";
import { fmt, fmtCr } from "@/lib/utils";

export default function AnalyticsPage() {
  const { data: pnl } = useQuery({ queryKey: ["pnl"], queryFn: fetchPnlSummary });
  const { data: trades = [] } = useQuery({ queryKey: ["trades-all"], queryFn: () => fetchTrades(200) });

  const daily: any[] = pnl?.daily_pnl ?? [];

  // Equity curve: cumulative P&L
  let cumulative = 0;
  const equityCurve = daily.map((d: any) => {
    cumulative += d.pnl;
    return { date: d.date, pnl: d.pnl, cumulative: Math.round(cumulative) };
  });

  // P&L distribution by tier
  const tierMap: Record<string, { wins: number; losses: number; pnl: number }> = {};
  for (const t of trades as any[]) {
    if (t.is_open || t.realized_pnl == null) continue;
    const tier = t.tier ?? "EQUITY";
    if (!tierMap[tier]) tierMap[tier] = { wins: 0, losses: 0, pnl: 0 };
    tierMap[tier].pnl += t.realized_pnl;
    if (t.realized_pnl > 0) tierMap[tier].wins++;
    else tierMap[tier].losses++;
  }
  const tierData = Object.entries(tierMap).map(([tier, v]) => ({ tier, ...v, pnl: Math.round(v.pnl) }));

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Analytics</h1>

      {/* Summary stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          ["Total Trades",   pnl?.total_trades ?? 0, ""],
          ["Realized P&L",   fmtCr(pnl?.realized_pnl), ""],
          ["Win Rate",       `${pnl?.win_rate ?? 0}%`, `${pnl?.wins ?? 0}W / ${pnl?.losses ?? 0}L`],
          ["Closed Trades",  pnl?.closed_trades ?? 0, ""],
        ].map(([label, value, sub]) => (
          <div key={label as string} className="bg-surface border border-border rounded-xl p-4">
            <p className="text-xs text-subtle mb-1">{label}</p>
            <p className="text-2xl font-mono font-bold">{value}</p>
            {sub && <p className="text-xs text-subtle mt-1">{sub}</p>}
          </div>
        ))}
      </div>

      {/* Equity curve */}
      <div className="bg-surface border border-border rounded-xl p-4">
        <h2 className="text-sm font-semibold mb-4">Cumulative P&L</h2>
        {equityCurve.length < 2 ? (
          <p className="text-subtle text-center py-8 text-sm">Not enough data yet.</p>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={equityCurve}>
              <defs>
                <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#22c55e" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#22c55e" stopOpacity={0}   />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 11 }} />
              <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} />
              <Tooltip
                contentStyle={{ backgroundColor: "#111827", border: "1px solid #1f2937" }}
                labelStyle={{ color: "#e5e7eb" }}
              />
              <Area type="monotone" dataKey="cumulative" stroke="#22c55e" fill="url(#pnlGrad)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Tier P&L */}
      {tierData.length > 0 && (
        <div className="bg-surface border border-border rounded-xl p-4">
          <h2 className="text-sm font-semibold mb-4">P&L by Tier</h2>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={tierData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="tier" tick={{ fill: "#6b7280", fontSize: 11 }} />
              <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} />
              <Tooltip
                contentStyle={{ backgroundColor: "#111827", border: "1px solid #1f2937" }}
              />
              <Bar dataKey="pnl" fill="#3b82f6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Trade log */}
      <div className="bg-surface border border-border rounded-xl overflow-hidden">
        <h2 className="text-sm font-semibold p-4 border-b border-border">Trade Log</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>Symbol</th><th>Tier</th><th>Action</th>
              <th>Entry</th><th>Exit</th><th>Qty</th>
              <th>P&L</th><th>Closed</th>
            </tr>
          </thead>
          <tbody>
            {(trades as any[]).filter(t => !t.is_open).slice(0, 50).map((t: any) => (
              <tr key={t.id}>
                <td className="font-mono">{t.symbol}</td>
                <td className="text-xs text-subtle">{t.tier}</td>
                <td className="text-xs">{t.action}</td>
                <td className="font-mono">₹{fmt(t.entry_price)}</td>
                <td className="font-mono">₹{fmt(t.exit_price)}</td>
                <td className="font-mono">{t.qty}</td>
                <td className={`font-mono font-semibold ${(t.realized_pnl ?? 0) >= 0 ? "text-bull" : "text-bear"}`}>
                  {fmtCr(t.realized_pnl)}
                </td>
                <td className="text-xs text-subtle">
                  {t.closed_at ? new Date(t.closed_at).toLocaleDateString("en-IN") : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

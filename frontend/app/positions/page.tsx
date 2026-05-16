"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchTrades } from "@/lib/api";
import { fmt, fmtCr } from "@/lib/utils";

export default function PositionsPage() {
  const { data: trades = [], isLoading } = useQuery({
    queryKey: ["trades-open"],
    queryFn: () => fetchTrades(100, true),
    refetchInterval: 10_000,
  });

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Open Positions</h1>

      <div className="bg-surface border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <p className="text-center text-subtle py-12">Loading...</p>
        ) : trades.length === 0 ? (
          <p className="text-center text-subtle py-12">No open positions.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th><th>Tier</th><th>Product</th>
                <th>Qty</th><th>Entry</th><th>SL</th>
                <th>Target</th><th>Unrealized</th><th>Entered</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t: any) => (
                <tr key={t.id}>
                  <td className="font-mono font-semibold text-accent">{t.symbol}</td>
                  <td className="text-xs text-subtle">{t.tier}</td>
                  <td className="text-xs">{t.product}</td>
                  <td className="font-mono">{t.qty}</td>
                  <td className="font-mono">₹{fmt(t.entry_price)}</td>
                  <td className="font-mono text-bear">₹{fmt(t.stop_loss)}</td>
                  <td className="font-mono text-bull">₹{fmt(t.target)}</td>
                  <td className="font-mono text-subtle">—</td>
                  <td className="text-xs text-subtle">
                    {t.executed_at ? new Date(t.executed_at).toLocaleString("en-IN") : "—"}
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

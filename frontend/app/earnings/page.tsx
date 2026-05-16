"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchEarningsCalendar } from "@/lib/api";

function SetupBadge({ rating }: { rating: string }) {
  const classes: Record<string, string> = {
    STRONG:   "bg-bull/20 text-bull border-bull/30",
    MODERATE: "bg-warn/20 text-warn border-warn/30",
    WEAK:     "bg-bear/20 text-bear border-bear/30",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-semibold border ${classes[rating] ?? classes.WEAK}`}>
      {rating}
    </span>
  );
}

function StarsRating({ count }: { count: number }) {
  return (
    <span className="text-warn text-sm tracking-tighter">
      {"★".repeat(Math.min(count, 5))}{"☆".repeat(Math.max(0, 5 - count))}
    </span>
  );
}

export default function EarningsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["earnings-calendar"],
    queryFn: fetchEarningsCalendar,
    refetchInterval: 3_600_000, // hourly
  });

  const calendar: any[] = data?.calendar ?? [];
  const upcoming  = calendar.filter(e => (e.days_until_results ?? 99) >= 0);
  const past      = calendar.filter(e => (e.days_until_results ?? 99) < 0);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Earnings Calendar</h1>

      {isLoading ? (
        <p className="text-subtle text-center py-12">Loading earnings data...</p>
      ) : (
        <>
          {/* Upcoming */}
          <section>
            <h2 className="text-sm font-semibold text-subtle uppercase mb-3">
              Upcoming ({upcoming.length})
            </h2>
            {upcoming.length === 0 ? (
              <p className="text-subtle text-sm">No upcoming results in next 30 days.</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {upcoming.map((e: any) => (
                  <div key={e.symbol} className="bg-surface border border-border rounded-xl p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-bold text-accent">{e.symbol}</span>
                      <SetupBadge rating={e.setup_rating} />
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-subtle">{e.result_date}</span>
                      <span className={`font-semibold ${e.days_until_results <= 3 ? "text-warn" : "text-text"}`}>
                        {e.days_until_results} days
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <StarsRating count={e.consecutive_beats} />
                      <span className="text-xs text-subtle">
                        {e.consecutive_beats} consecutive beats
                      </span>
                    </div>
                    {e.eps_estimate && (
                      <p className="text-xs text-subtle">EPS Est: ₹{e.eps_estimate}</p>
                    )}
                    <p className="text-xs text-subtle italic">{e.purpose}</p>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Past results */}
          {past.length > 0 && (
            <section>
              <h2 className="text-sm font-semibold text-subtle uppercase mb-3">
                Recent Results ({past.length})
              </h2>
              <div className="bg-surface border border-border rounded-xl overflow-hidden">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Symbol</th><th>Date</th>
                      <th>EPS Est</th><th>Consecutive Beats</th><th>Setup</th>
                    </tr>
                  </thead>
                  <tbody>
                    {past.map((e: any) => (
                      <tr key={e.symbol}>
                        <td className="font-mono font-semibold text-accent">{e.symbol}</td>
                        <td className="text-subtle text-xs">{e.result_date}</td>
                        <td className="font-mono">{e.eps_estimate ?? "—"}</td>
                        <td><StarsRating count={e.consecutive_beats} /></td>
                        <td><SetupBadge rating={e.setup_rating} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}

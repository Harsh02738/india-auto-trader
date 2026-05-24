"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { createChart, IChartApi, ISeriesApi } from "lightweight-charts";

type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

const API_BASE = "/api";

export default function IntradayPage() {
  const chartRef  = useRef<HTMLDivElement>(null);
  const chartApi  = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  const [symbols, setSymbols]     = useState<string[]>([]);
  const [selected, setSelected]   = useState<string>("");
  const [bars, setBars]           = useState<Bar[]>([]);
  const [error, setError]         = useState<string>("");
  const [lastUpdate, setLastUpdate] = useState<string>("");

  const fetchSymbols = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/intraday`);
      if (!res.ok) return;
      const data: string[] = await res.json();
      setSymbols(data);
      if (!selected && data.length > 0) setSelected(data[0]);
    } catch {}
  }, [selected]);

  const fetchBars = useCallback(async (sym: string) => {
    if (!sym) return;
    try {
      const res = await fetch(`${API_BASE}/intraday/${sym}?bars=390`);
      if (!res.ok) {
        setError(`No intraday data available for ${sym} yet`);
        setBars([]);
        return;
      }
      const data: Bar[] = await res.json();
      if (!Array.isArray(data) || data.length === 0) {
        setError(`No bars recorded for ${sym} yet`);
        setBars([]);
        return;
      }
      setBars(data);
      setError("");
      setLastUpdate(new Date().toLocaleTimeString("en-IN", { hour12: false }));
    } catch (e) {
      setError(`Fetch error: ${e}`);
    }
  }, []);

  useEffect(() => { fetchSymbols(); }, []);

  useEffect(() => {
    if (!selected) return;
    fetchBars(selected);
    const timer = setInterval(() => fetchBars(selected), 60_000);
    return () => clearInterval(timer);
  }, [selected, fetchBars]);

  // Create chart once on mount
  useEffect(() => {
    if (!chartRef.current || chartApi.current) return;

    const chart = createChart(chartRef.current, {
      width:  chartRef.current.clientWidth,
      height: 440,
      layout: {
        background: { color: "#0d1117" },
        textColor:  "#c9d1d9",
      },
      grid: {
        vertLines: { color: "#21262d" },
        horzLines: { color: "#21262d" },
      },
      crosshair: { mode: 1 },
      timeScale: { timeVisible: true, secondsVisible: false },
    });

    chartApi.current  = chart;
    candleRef.current = chart.addCandlestickSeries({
      upColor:       "#22c55e",
      downColor:     "#ef4444",
      borderVisible: false,
      wickUpColor:   "#22c55e",
      wickDownColor: "#ef4444",
    });
    volumeRef.current = chart.addHistogramSeries({
      color:       "#3b82f680",
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    const handleResize = () => {
      if (chartRef.current) chart.resize(chartRef.current.clientWidth, 440);
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartApi.current = null;
    };
  }, []);

  // Push new bar data to chart
  useEffect(() => {
    if (!candleRef.current || !volumeRef.current || bars.length === 0) return;
    const candles = bars.map((b) => ({
      time:  b.time as any,
      open:  b.open,
      high:  b.high,
      low:   b.low,
      close: b.close,
    }));
    const volumes = bars.map((b) => ({
      time:  b.time as any,
      value: b.volume,
      color: b.close >= b.open ? "#22c55e40" : "#ef444440",
    }));
    candleRef.current.setData(candles);
    volumeRef.current.setData(volumes);
    chartApi.current?.timeScale().fitContent();
  }, [bars]);

  const lastBar  = bars[bars.length - 1];
  const firstBar = bars[0];
  const dayHigh  = bars.length ? Math.max(...bars.map((b) => b.high))  : 0;
  const dayLow   = bars.length ? Math.min(...bars.map((b) => b.low))   : 0;
  const change   = firstBar && lastBar ? lastBar.close - firstBar.open : 0;
  const changePct = firstBar?.open ? (change / firstBar.open) * 100 : 0;

  return (
    <div className="p-4 space-y-4 max-w-7xl mx-auto">
      {/* Paper trading banner */}
      <div className="bg-green-950/60 border border-green-600/50 text-green-400 font-semibold text-center py-2.5 rounded-lg text-sm tracking-wide">
        📄 PAPER TRADING MODE — Simulated trades only · No real orders placed
      </div>

      {/* Controls row */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-subtle text-sm font-medium">Symbol:</label>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="bg-surface border border-border rounded-md px-3 py-1.5 text-sm text-text focus:outline-none focus:border-accent"
        >
          {symbols.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
          {symbols.length === 0 && (
            <option value="">No data yet — market not open?</option>
          )}
        </select>

        <button
          onClick={() => fetchBars(selected)}
          disabled={!selected}
          className="px-3 py-1.5 bg-accent/20 border border-accent/40 rounded-md text-xs text-accent hover:bg-accent/30 disabled:opacity-40"
        >
          ↺ Refresh
        </button>

        {lastUpdate && (
          <span className="text-subtle text-xs ml-auto">
            Updated: {lastUpdate} · Auto-refresh every 60s
          </span>
        )}
      </div>

      {/* Stat chips */}
      {lastBar && (
        <div className="flex flex-wrap gap-3">
          {[
            { label: "Open",    value: `₹${firstBar?.open?.toFixed(2) ?? "—"}` },
            { label: "High",    value: `₹${dayHigh.toFixed(2)}` },
            { label: "Low",     value: `₹${dayLow.toFixed(2)}` },
            { label: "LTP",     value: `₹${lastBar.close.toFixed(2)}` },
            {
              label: "Change",
              value: `${change >= 0 ? "+" : ""}${change.toFixed(2)} (${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%)`,
              color: change >= 0 ? "text-green-400" : "text-red-400",
            },
            { label: "Bars",    value: `${bars.length}` },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-surface border border-border rounded-lg px-4 py-2">
              <p className="text-subtle text-xs">{label}</p>
              <p className={`font-mono text-sm font-medium ${color ?? "text-text"}`}>{value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Chart */}
      <div className="bg-surface border border-border rounded-lg overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-3">
          <span className="font-mono font-semibold text-text">{selected || "—"}</span>
          <span className="text-subtle text-xs">1-minute OHLCV candles</span>
        </div>

        {error ? (
          <div className="flex items-center justify-center h-64 text-subtle text-sm">
            {error}
          </div>
        ) : (
          <div ref={chartRef} className="w-full" />
        )}
      </div>
    </div>
  );
}

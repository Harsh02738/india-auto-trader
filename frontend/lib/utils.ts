import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmt(n: number | null | undefined, decimals = 2): string {
  if (n === null || n === undefined) return "—";
  return n.toFixed(decimals);
}

export function fmtCr(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(1)}Cr`;
  if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(1)}L`;
  return `₹${n.toFixed(0)}`;
}

export function fmtPct(n: number | null | undefined, signed = false): string {
  if (n === null || n === undefined) return "—";
  const s = `${Math.abs(n).toFixed(2)}%`;
  if (!signed) return s;
  return n >= 0 ? `+${s}` : `-${s}`;
}

export function scoreColor(score: number): string {
  if (score >= 0.70) return "text-bull";
  if (score >= 0.60) return "text-accent";
  if (score >= 0.50) return "text-text";
  return "text-bear";
}

export function signalBadge(action: string): string {
  if (action === "BUY")  return "bg-bull/20 text-bull border border-bull/30";
  if (action === "SELL") return "bg-bear/20 text-bear border border-bear/30";
  return "bg-muted/30 text-subtle border border-muted/30";
}

export function circuitColor(state: string): string {
  if (state === "TRIPPED")  return "text-bear";
  if (state === "WARNING")  return "text-warn";
  return "text-bull";
}

import axios from "axios";

export const api = axios.create({
  baseURL: "/api",
  timeout: 10_000,
});

export async function fetchSignals() {
  const { data } = await api.get("/signals");
  return data as any[];
}

export async function fetchSignalDetail(symbol: string) {
  const { data } = await api.get(`/signals/${symbol}`);
  return data;
}

export async function fetchSnapshot() {
  const { data } = await api.get("/portfolio/snapshot");
  return data;
}

export async function fetchFiiDii() {
  const { data } = await api.get("/portfolio/fii-dii");
  return data;
}

export async function fetchOptionChain(symbol: string) {
  const { data } = await api.get(`/options/${symbol}/chain`);
  return data;
}

export async function fetchMarketPcr() {
  const { data } = await api.get("/options/pcr");
  return data;
}

export async function fetchEarningsCalendar() {
  const { data } = await api.get("/earnings/calendar");
  return data;
}

export async function fetchEarningsResults(symbol: string) {
  const { data } = await api.get(`/earnings/${symbol}/results`);
  return data;
}

export async function fetchPennyCandidates() {
  const { data } = await api.get("/penny/candidates");
  return data;
}

export async function fetchPnlSummary() {
  const { data } = await api.get("/pnl/summary");
  return data;
}

export async function fetchTrades(limit = 50, openOnly = false) {
  const { data } = await api.get(`/trades?limit=${limit}&open_only=${openOnly}`);
  return data as any[];
}

export async function fetchIntradayBars(symbol: string, bars = 390) {
  const { data } = await api.get(`/intraday/${symbol}?bars=${bars}`);
  return data as any[];
}

export async function fetchIntradaySymbols() {
  const { data } = await api.get("/intraday");
  return data as string[];
}

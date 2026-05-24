import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/supabase";
import { readDataFile } from "@/lib/datafiles";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> }
) {
  const { symbol } = await params;
  const sym = symbol.toUpperCase();

  let signal = null;
  try {
    const res = await backendFetch(`/signals/${sym}`);
    if (res.ok) {
      const body = await res.json();
      signal = body.signal ?? null;
    }
  } catch {}

  return NextResponse.json({
    signal,
    ohlcv:        readDataFile(`market/${sym}_ohlcv.json`),
    fundamentals: readDataFile(`fundamentals/${sym}_fund.json`),
    sentiment:    readDataFile(`sentiment/${sym}_sent.json`),
    news:         readDataFile(`news/${sym}_news.json`),
  });
}

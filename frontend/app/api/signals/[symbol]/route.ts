import { NextResponse } from "next/server";
import { createServerClient } from "@/lib/supabase";
import { readDataFile } from "@/lib/datafiles";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> }
) {
  const { symbol } = await params;
  const sym = symbol.toUpperCase();
  const db = createServerClient();

  const { data: signal } = await db
    .from("signals")
    .select("*")
    .eq("symbol", sym)
    .order("created_at", { ascending: false })
    .limit(1)
    .single();

  return NextResponse.json({
    signal:       signal ?? null,
    ohlcv:        readDataFile(`market/${sym}_ohlcv.json`),
    fundamentals: readDataFile(`fundamentals/${sym}_fund.json`),
    sentiment:    readDataFile(`sentiment/${sym}_sent.json`),
    news:         readDataFile(`news/${sym}_news.json`),
  });
}

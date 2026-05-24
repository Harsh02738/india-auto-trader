import { NextRequest, NextResponse } from "next/server";
import { backendFetch } from "@/lib/supabase";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ symbol: string }> }
) {
  const { symbol } = await params;
  const { searchParams } = new URL(req.url);
  const bars = searchParams.get("bars") ?? "390";
  const res = await backendFetch(`/intraday/${symbol.toUpperCase()}?bars=${bars}`);
  if (!res.ok) return NextResponse.json({ error: "No intraday data" }, { status: res.status });
  return NextResponse.json(await res.json());
}

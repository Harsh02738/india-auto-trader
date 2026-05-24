import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/supabase";

export async function GET() {
  const res = await backendFetch("/pnl/summary");
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}

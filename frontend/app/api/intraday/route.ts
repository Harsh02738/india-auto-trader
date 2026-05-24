import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/supabase";

export async function GET() {
  const res = await backendFetch("/intraday");
  if (!res.ok) return NextResponse.json([], { status: res.status });
  return NextResponse.json(await res.json());
}

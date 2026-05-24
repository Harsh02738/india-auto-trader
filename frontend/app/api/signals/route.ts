import { NextRequest, NextResponse } from "next/server";
import { backendFetch } from "@/lib/supabase";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const limit = searchParams.get("limit") ?? "20";
  const res = await backendFetch(`/signals?limit=${limit}`);
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}

import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/supabase";

export async function GET() {
  const res = await backendFetch("/portfolio/snapshot");
  const data = await res.json();
  return NextResponse.json(data, { status: res.ok ? 200 : res.status });
}

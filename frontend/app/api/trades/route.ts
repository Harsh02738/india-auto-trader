import { NextRequest, NextResponse } from "next/server";
import { createServerClient } from "@/lib/supabase";

export async function GET(req: NextRequest) {
  const db = createServerClient();
  const { searchParams } = new URL(req.url);
  const limit    = Number(searchParams.get("limit") ?? 50);
  const openOnly = searchParams.get("open_only") === "true";

  let query = db
    .from("trades")
    .select("*")
    .order("executed_at", { ascending: false })
    .limit(limit);

  if (openOnly) query = query.eq("is_open", true);

  const { data, error } = await query;
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json(data ?? []);
}

export async function POST(req: NextRequest) {
  const db = createServerClient();
  const body = await req.json();

  const { data, error } = await db
    .from("trades")
    .insert(body)
    .select()
    .single();

  if (error) return NextResponse.json({ error: error.message }, { status: 400 });
  return NextResponse.json(data, { status: 201 });
}

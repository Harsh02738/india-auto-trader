import { NextResponse } from "next/server";
import { readDataFile } from "@/lib/datafiles";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> }
) {
  const { symbol } = await params;
  const data = readDataFile(`earnings/${symbol.toUpperCase()}_results.json`);
  if (!data) return NextResponse.json({ error: "Not found" }, { status: 404 });
  return NextResponse.json(data);
}

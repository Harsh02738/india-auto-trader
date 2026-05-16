import { NextResponse } from "next/server";
import { readDataFile } from "@/lib/datafiles";

export async function GET() {
  const data = readDataFile("sentiment/fii_dii.json");
  return NextResponse.json(data ?? {});
}

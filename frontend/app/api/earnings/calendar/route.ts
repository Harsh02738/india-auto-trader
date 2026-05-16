import { NextResponse } from "next/server";
import { readDataFile } from "@/lib/datafiles";

export async function GET() {
  return NextResponse.json(readDataFile("earnings/calendar.json") ?? { calendar: [] });
}

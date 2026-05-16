import fs from "fs";
import path from "path";

// Resolve the data/ directory relative to the project root (two levels up from frontend/)
const DATA_ROOT = path.resolve(process.cwd(), "..", "data");

export function readDataFile(relPath: string): unknown | null {
  try {
    const fullPath = path.join(DATA_ROOT, relPath);
    const raw = fs.readFileSync(fullPath, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

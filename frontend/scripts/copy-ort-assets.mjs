import { cp, mkdir, readdir } from "node:fs/promises";
import { join } from "node:path";

const source = join(process.cwd(), "node_modules", "onnxruntime-web", "dist");
const destination = join(process.cwd(), "public", "ort");
await mkdir(destination, { recursive: true });
for (const entry of await readdir(source)) {
  if (entry.endsWith(".wasm") || entry.endsWith(".mjs")) await cp(join(source, entry), join(destination, entry));
}

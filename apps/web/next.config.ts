import type { NextConfig } from "next";
import fs from "node:fs";
import path from "node:path";

// The monorepo keeps a single shared .env at the repo root so the Python
// worker and the Next.js app see the same values. Next.js only auto-loads
// from its own project root by default, so we read the repo-root .env
// ourselves and populate process.env BEFORE Next.js spawns any workers.
//
// We do this with a tiny inline parser instead of @next/env because
// `loadEnvConfig`'s effects don't always propagate through Turbopack's
// request-handler worker boundary in dev.
function loadRootEnv() {
  const candidates = [
    path.resolve(process.cwd(), "..", "..", ".env"),
    path.resolve(process.cwd(), ".env"),
  ];
  for (const envPath of candidates) {
    if (!fs.existsSync(envPath)) continue;
    const text = fs.readFileSync(envPath, "utf8");
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq < 1) continue;
      const key = line.slice(0, eq).trim();
      if (!/^[A-Z_][A-Z0-9_]*$/i.test(key)) continue;
      // Real env values win over the file — same semantics as dotenv.
      if (process.env[key] !== undefined && process.env[key] !== "") continue;
      let val = line.slice(eq + 1).trim();
      if (
        (val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))
      ) {
        val = val.slice(1, -1);
      }
      process.env[key] = val;
    }
  }
}

loadRootEnv();

const nextConfig: NextConfig = {
  /* config options here */
};

export default nextConfig;

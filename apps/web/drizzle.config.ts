import path from "node:path";
import { defineConfig } from "drizzle-kit";

const dbUrl = process.env.DATABASE_URL ?? "file:./data/app.db";
const raw = dbUrl.startsWith("file:") ? dbUrl.slice("file:".length) : dbUrl;
const dbPath = path.isAbsolute(raw)
  ? raw
  : path.resolve(__dirname, "..", "..", raw);

export default defineConfig({
  schema: "./lib/db/schema.ts",
  out: "./lib/db/migrations",
  dialect: "sqlite",
  dbCredentials: { url: `file:${dbPath}` },
  verbose: true,
  strict: true,
});

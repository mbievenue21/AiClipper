/**
 * Singleton Drizzle client.
 *
 * The same SQLite file is also opened by the Python worker (via SQLAlchemy).
 * We enable WAL so that simultaneous reads from Next.js and writes from
 * the worker don't block each other.
 */
import "server-only";

import path from "node:path";
import fs from "node:fs";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";

import * as schema from "./schema";

declare global {
  // eslint-disable-next-line no-var
  var __aiclipper_db: ReturnType<typeof drizzle> | undefined;
  // eslint-disable-next-line no-var
  var __aiclipper_sqlite: Database.Database | undefined;
}

function resolveDatabasePath(): string {
  const fromEnv = process.env.DATABASE_URL;
  // We accept either "file:./data/app.db" (Drizzle convention) or a raw path.
  const raw = fromEnv?.startsWith("file:")
    ? fromEnv.slice("file:".length)
    : (fromEnv ?? "./data/app.db");

  // Resolve relative to the workspace root (two levels up from apps/web).
  const root = path.resolve(process.cwd(), "..", "..");
  const abs = path.isAbsolute(raw) ? raw : path.resolve(root, raw);

  fs.mkdirSync(path.dirname(abs), { recursive: true });
  return abs;
}

function createClient() {
  const dbPath = resolveDatabasePath();
  const sqlite = new Database(dbPath);
  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("foreign_keys = ON");
  sqlite.pragma("busy_timeout = 5000");
  sqlite.pragma("synchronous = NORMAL"); // safe with WAL, much faster

  const db = drizzle(sqlite, { schema });

  if (process.env.NODE_ENV !== "production") {
    globalThis.__aiclipper_db = db;
    globalThis.__aiclipper_sqlite = sqlite;
  }

  return { db, sqlite };
}

const existing = globalThis.__aiclipper_db
  ? { db: globalThis.__aiclipper_db, sqlite: globalThis.__aiclipper_sqlite! }
  : createClient();

export const db = existing.db;
export const sqlite = existing.sqlite;
export { schema };

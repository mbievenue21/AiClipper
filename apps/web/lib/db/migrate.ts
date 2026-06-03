/**
 * Applies all pending migrations from ./lib/db/migrations against the
 * SQLite file. Safe to run repeatedly; Drizzle tracks applied migrations
 * in a `__drizzle_migrations` table.
 *
 *   pnpm db:migrate
 */
import path from "node:path";
import fs from "node:fs";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";

function resolveDatabasePath(): string {
  const raw = (process.env.DATABASE_URL ?? "./data/app.db").replace(
    /^file:/,
    "",
  );
  const root = path.resolve(__dirname, "..", "..", "..", "..");
  const abs = path.isAbsolute(raw) ? raw : path.resolve(root, raw);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  return abs;
}

const dbPath = resolveDatabasePath();
console.log(`[migrate] using database at ${dbPath}`);

const sqlite = new Database(dbPath);
sqlite.pragma("journal_mode = WAL");
sqlite.pragma("foreign_keys = ON");

const db = drizzle(sqlite);
const migrationsFolder = path.resolve(__dirname, "migrations");

migrate(db, { migrationsFolder });
console.log("[migrate] complete");
sqlite.close();

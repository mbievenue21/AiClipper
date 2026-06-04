/**
 * Tiny migration runner that does NOT go through tsx — we hit a
 * better-sqlite3 ABI mismatch when tsx + the Node version pin don't
 * agree. Reads `apps/web/lib/db/migrations/_journal.json`, applies any
 * migration not yet recorded in `__drizzle_migrations`. Idempotent.
 *
 * Usage:
 *   node lib/db/scripts/apply-migration.mjs
 */
import Database from "better-sqlite3";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const dbPath = path.resolve(process.cwd(), "..", "..", "data", "app.db");
const migrationsDir = path.resolve(process.cwd(), "lib", "db", "migrations");

if (!fs.existsSync(dbPath)) {
  console.error("no db at", dbPath);
  process.exit(1);
}

const db = new Database(dbPath);

// Drizzle's tracking table — same schema as drizzle-kit creates.
db.exec(`
  CREATE TABLE IF NOT EXISTS __drizzle_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL,
    created_at NUMERIC
  )
`);

const journalPath = path.join(migrationsDir, "meta", "_journal.json");
const journal = JSON.parse(fs.readFileSync(journalPath, "utf8"));

const applied = new Set(
  db
    .prepare("SELECT hash FROM __drizzle_migrations")
    .all()
    .map((r) => r.hash),
);

let count = 0;
for (const entry of journal.entries) {
  const sqlPath = path.join(migrationsDir, `${entry.tag}.sql`);
  if (!fs.existsSync(sqlPath)) {
    console.log(`skip ${entry.tag} (no sql file)`);
    continue;
  }
  const sql = fs.readFileSync(sqlPath, "utf8");
  const hash = crypto.createHash("sha256").update(sql).digest("hex");
  if (applied.has(hash)) {
    console.log(`already applied: ${entry.tag}`);
    continue;
  }
  console.log(`applying: ${entry.tag}`);
  const statements = sql
    .split(/--> statement-breakpoint/)
    .map((s) => s.trim())
    .filter(Boolean);
  const tx = db.transaction(() => {
    for (const stmt of statements) {
      db.exec(stmt);
    }
    db.prepare(
      "INSERT INTO __drizzle_migrations (hash, created_at) VALUES (?, ?)",
    ).run(hash, Date.now());
  });
  tx();
  count++;
}

console.log(`done. ${count} migration(s) applied.`);
db.close();

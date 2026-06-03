/**
 * Inserts a few sample projects so the homepage isn't empty during dev.
 *
 *   pnpm --filter web exec tsx scripts/seed.ts
 */
import path from "node:path";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";

import * as schema from "../lib/db/schema";

async function main() {
  const dbPath = path.resolve(__dirname, "..", "..", "..", "data", "app.db");
  const sqlite = new Database(dbPath);
  sqlite.pragma("foreign_keys = ON");
  const db = drizzle(sqlite, { schema });

  await db.insert(schema.projects).values([
    {
      name: "Sample stream VOD #1",
      sourceUrl: "https://www.twitch.tv/videos/123456",
      sourceType: "twitch",
      status: "pending",
    },
    {
      name: "YouTube interview (5h)",
      sourceUrl: "https://www.youtube.com/watch?v=xyz",
      sourceType: "youtube",
      status: "transcribing",
    },
    {
      name: "Old uploaded clip reel",
      sourceType: "upload",
      status: "ready",
    },
  ]);

  const all = await db.select().from(schema.projects);
  console.log(`Total projects in DB: ${all.length}`);
  sqlite.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

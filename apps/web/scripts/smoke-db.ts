/**
 * Schema smoke test. Run with: `pnpm --filter web exec tsx scripts/smoke-db.ts`
 *
 * Opens its own SQLite connection (the singleton in lib/db/client.ts is
 * gated by `import "server-only"` and can't run outside Next.js).
 */
import path from "node:path";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { eq } from "drizzle-orm";

import * as schema from "../lib/db/schema";

async function main() {
  const dbPath = path.resolve(__dirname, "..", "..", "..", "data", "app.db");
  const sqlite = new Database(dbPath);
  sqlite.pragma("foreign_keys = ON");
  const db = drizzle(sqlite, { schema });

  const tables = sqlite
    .prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '__drizzle%' ORDER BY name",
    )
    .all() as Array<{ name: string }>;

  console.log(
    `Tables (${tables.length}):`,
    tables.map((t) => t.name).join(", "),
  );

  const [project] = await db
    .insert(schema.projects)
    .values({
      name: "Smoke test project",
      sourceUrl: "https://youtube.com/watch?v=test",
      sourceType: "youtube",
    })
    .returning();
  console.log("Inserted project:", {
    id: project.id,
    status: project.status,
    createdAt: project.createdAt,
  });

  const [video] = await db
    .insert(schema.videos)
    .values({ projectId: project.id, filePath: "test/source.mp4" })
    .returning();
  console.log("Inserted video:", { id: video.id, filePath: video.filePath });

  const videosBefore = await db
    .select()
    .from(schema.videos)
    .where(eq(schema.videos.projectId, project.id));
  console.log(`Videos before cascade: ${videosBefore.length}`);

  await db.delete(schema.projects).where(eq(schema.projects.id, project.id));

  const videosAfter = await db
    .select()
    .from(schema.videos)
    .where(eq(schema.videos.projectId, project.id));
  console.log(`Videos after cascade:  ${videosAfter.length}`);

  if (videosAfter.length !== 0) {
    console.error("FAIL: cascade delete did not remove video rows");
    process.exit(1);
  }

  console.log("OK \u2014 schema works end-to-end with cascading deletes");
  sqlite.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

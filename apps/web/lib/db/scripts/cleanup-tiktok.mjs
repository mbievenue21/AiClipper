/**
 * One-shot cleanup script: remove any `tiktok` rows left over from when
 * TikTok was a supported platform. Run from apps/web:
 *
 *   node lib/db/scripts/cleanup-tiktok.mjs
 *
 * Idempotent — running it again does nothing.
 */
import Database from "better-sqlite3";
import path from "node:path";

const dbPath = path.resolve(process.cwd(), "..", "..", "data", "app.db");
const db = new Database(dbPath);

const before = {
  accounts: db.prepare("SELECT platform, COUNT(*) AS n FROM accounts GROUP BY platform").all(),
  scheduled_uploads: db
    .prepare("SELECT platform, COUNT(*) AS n FROM scheduled_uploads GROUP BY platform")
    .all(),
};

console.log("before:", JSON.stringify(before, null, 2));

const tx = db.transaction(() => {
  const acctDel = db.prepare("DELETE FROM accounts WHERE platform = 'tiktok'").run();
  const upDel = db.prepare("DELETE FROM scheduled_uploads WHERE platform = 'tiktok'").run();
  console.log(`deleted ${acctDel.changes} tiktok accounts, ${upDel.changes} tiktok uploads`);
});
tx();

const after = {
  accounts: db.prepare("SELECT platform, COUNT(*) AS n FROM accounts GROUP BY platform").all(),
  scheduled_uploads: db
    .prepare("SELECT platform, COUNT(*) AS n FROM scheduled_uploads GROUP BY platform")
    .all(),
};
console.log("after:", JSON.stringify(after, null, 2));
db.close();

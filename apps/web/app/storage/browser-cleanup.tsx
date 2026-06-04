"use client";

/**
 * Client-side cleanup for the user's browser. Surveys and clears
 * localStorage, sessionStorage, cookies for this origin, IndexedDB, and
 * the Cache Storage (used by service workers / PWA layers).
 *
 * All of this runs in the browser — there's no server round-trip. We
 * intentionally don't try to clear HTTP-only cookies (the OAuth state
 * cookies) because they're scoped to the API routes and expire on their
 * own; touching them would break in-flight OAuth handshakes.
 */
import { useCallback, useEffect, useState, useTransition } from "react";
import { Database, HardDrive, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type BrowserSurvey = {
  localStorageKeys: number;
  localStorageBytes: number;
  sessionStorageKeys: number;
  sessionStorageBytes: number;
  jsCookieCount: number;
  indexedDbCount: number | null; // null when the API is not available
  cacheStorageCount: number | null;
  storageEstimate: { usageBytes: number | null; quotaBytes: number | null };
};

function bytesOfStorage(s: Storage): number {
  let total = 0;
  for (let i = 0; i < s.length; i++) {
    const key = s.key(i);
    if (!key) continue;
    const val = s.getItem(key) ?? "";
    total += key.length + val.length;
  }
  // UTF-16 — each char ~= 2 bytes. Conservative estimate.
  return total * 2;
}

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}

async function surveyBrowser(): Promise<BrowserSurvey> {
  const survey: BrowserSurvey = {
    localStorageKeys: 0,
    localStorageBytes: 0,
    sessionStorageKeys: 0,
    sessionStorageBytes: 0,
    jsCookieCount: 0,
    indexedDbCount: null,
    cacheStorageCount: null,
    storageEstimate: { usageBytes: null, quotaBytes: null },
  };

  try {
    survey.localStorageKeys = window.localStorage.length;
    survey.localStorageBytes = bytesOfStorage(window.localStorage);
  } catch {
    // private browsing or disabled storage
  }
  try {
    survey.sessionStorageKeys = window.sessionStorage.length;
    survey.sessionStorageBytes = bytesOfStorage(window.sessionStorage);
  } catch {
    /* noop */
  }
  try {
    survey.jsCookieCount = document.cookie
      ? document.cookie.split(";").filter(Boolean).length
      : 0;
  } catch {
    /* noop */
  }
  if ("indexedDB" in window && "databases" in indexedDB) {
    try {
      const dbs = await (
        indexedDB as IDBFactory & {
          databases: () => Promise<IDBDatabaseInfo[]>;
        }
      ).databases();
      survey.indexedDbCount = dbs.length;
    } catch {
      survey.indexedDbCount = null;
    }
  }
  if ("caches" in window) {
    try {
      const names = await caches.keys();
      survey.cacheStorageCount = names.length;
    } catch {
      survey.cacheStorageCount = null;
    }
  }
  if ("storage" in navigator && "estimate" in navigator.storage) {
    try {
      const est = await navigator.storage.estimate();
      survey.storageEstimate = {
        usageBytes: typeof est.usage === "number" ? est.usage : null,
        quotaBytes: typeof est.quota === "number" ? est.quota : null,
      };
    } catch {
      /* noop */
    }
  }
  return survey;
}

async function clearLocalStorage(): Promise<number> {
  const before = window.localStorage.length;
  window.localStorage.clear();
  return before;
}
async function clearSessionStorage(): Promise<number> {
  const before = window.sessionStorage.length;
  window.sessionStorage.clear();
  return before;
}
async function clearJsCookies(): Promise<number> {
  // We can only nuke cookies that are NOT HttpOnly. Walk them and overwrite
  // each with an expired date on the current path + every parent path.
  if (!document.cookie) return 0;
  const all = document.cookie.split(";").map((c) => c.split("=")[0].trim());
  const hostname = window.location.hostname;
  const paths = ["/"];
  const segments = window.location.pathname
    .split("/")
    .filter(Boolean);
  let acc = "";
  for (const s of segments) {
    acc += `/${s}`;
    paths.push(acc);
  }
  for (const name of all) {
    if (!name) continue;
    for (const p of paths) {
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=${p}`;
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=${p}; domain=${hostname}`;
    }
  }
  return all.filter(Boolean).length;
}
async function clearIndexedDb(): Promise<number> {
  if (!("indexedDB" in window) || !("databases" in indexedDB)) return 0;
  const dbs = await (
    indexedDB as IDBFactory & { databases: () => Promise<IDBDatabaseInfo[]> }
  ).databases();
  await Promise.all(
    dbs.map(
      (d) =>
        new Promise<void>((resolve) => {
          if (!d.name) return resolve();
          const req = indexedDB.deleteDatabase(d.name);
          req.onsuccess = () => resolve();
          req.onerror = () => resolve();
          req.onblocked = () => resolve();
        }),
    ),
  );
  return dbs.length;
}
async function clearCaches(): Promise<number> {
  if (!("caches" in window)) return 0;
  const names = await caches.keys();
  await Promise.all(names.map((n) => caches.delete(n)));
  return names.length;
}

export function BrowserCleanup() {
  const [survey, setSurvey] = useState<BrowserSurvey | null>(null);
  const [pending, startTransition] = useTransition();

  const refresh = useCallback(() => {
    startTransition(async () => {
      try {
        setSurvey(await surveyBrowser());
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const run = (label: string, fn: () => Promise<number>, unit: string) => () => {
    startTransition(async () => {
      try {
        const n = await fn();
        toast.success(`${label}: cleared ${n} ${unit}.`);
        setSurvey(await surveyBrowser());
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    });
  };

  const nuke = () => {
    if (
      !window.confirm(
        "Clear ALL browser-side state for this origin?\n\n• localStorage\n• sessionStorage\n• JS-visible cookies\n• IndexedDB databases\n• CacheStorage (Service Worker caches)\n\nYour OAuth state cookies (HttpOnly) and the actual server tokens are NOT affected.",
      )
    ) {
      return;
    }
    startTransition(async () => {
      try {
        const ls = await clearLocalStorage();
        const ss = await clearSessionStorage();
        const ck = await clearJsCookies();
        const idb = await clearIndexedDb();
        const cs = await clearCaches();
        toast.success(
          `Cleared ${ls} localStorage, ${ss} sessionStorage, ${ck} cookie(s), ${idb} IndexedDB, ${cs} cache(s).`,
        );
        setSurvey(await surveyBrowser());
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    });
  };

  const est = survey?.storageEstimate;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <HardDrive className="size-4" />
            Browser storage
          </CardTitle>
          <Button
            size="sm"
            variant="ghost"
            onClick={refresh}
            disabled={pending}
          >
            {pending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <RefreshCw className="size-3.5" />
            )}
            Refresh
          </Button>
        </div>
        <CardDescription>
          Anything stored by this app in <em>your</em> browser. Server-side
          OAuth tokens, the SQLite DB, and HttpOnly cookies are NOT touched
          by these buttons.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {survey == null ? (
          <p className="text-sm text-muted-foreground">Reading browser state…</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
              <Stat
                label="localStorage"
                primary={`${survey.localStorageKeys} key${
                  survey.localStorageKeys === 1 ? "" : "s"
                }`}
                secondary={formatBytes(survey.localStorageBytes)}
              />
              <Stat
                label="sessionStorage"
                primary={`${survey.sessionStorageKeys} key${
                  survey.sessionStorageKeys === 1 ? "" : "s"
                }`}
                secondary={formatBytes(survey.sessionStorageBytes)}
              />
              <Stat
                label="JS cookies"
                primary={`${survey.jsCookieCount}`}
                secondary="(HttpOnly cookies hidden)"
              />
              <Stat
                label="IndexedDB"
                primary={
                  survey.indexedDbCount == null
                    ? "n/a"
                    : `${survey.indexedDbCount} db${
                        survey.indexedDbCount === 1 ? "" : "s"
                      }`
                }
              />
              <Stat
                label="CacheStorage"
                primary={
                  survey.cacheStorageCount == null
                    ? "n/a"
                    : `${survey.cacheStorageCount} cache${
                        survey.cacheStorageCount === 1 ? "" : "s"
                      }`
                }
              />
              <Stat
                label="Quota usage"
                primary={
                  est?.usageBytes != null && est.quotaBytes != null
                    ? `${formatBytes(est.usageBytes)} / ${formatBytes(est.quotaBytes)}`
                    : "n/a"
                }
              />
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <Button
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={run(
                  "localStorage",
                  clearLocalStorage,
                  "key(s)",
                )}
              >
                <Trash2 className="size-3.5" /> Clear localStorage
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={run(
                  "sessionStorage",
                  clearSessionStorage,
                  "key(s)",
                )}
              >
                <Trash2 className="size-3.5" /> Clear sessionStorage
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={run("Cookies", clearJsCookies, "cookie(s)")}
              >
                <Trash2 className="size-3.5" /> Clear JS cookies
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={run("IndexedDB", clearIndexedDb, "db(s)")}
              >
                <Database className="size-3.5" /> Clear IndexedDB
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={run("CacheStorage", clearCaches, "cache(s)")}
              >
                <Trash2 className="size-3.5" /> Clear CacheStorage
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={pending}
                onClick={nuke}
              >
                <Trash2 className="size-3.5" /> Clear everything
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  primary,
  secondary,
}: {
  label: string;
  primary: string;
  secondary?: string;
}) {
  return (
    <div className="rounded-md border p-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="text-sm font-medium">{primary}</p>
      {secondary && (
        <p className="text-[10px] text-muted-foreground">{secondary}</p>
      )}
    </div>
  );
}

export function BrowserBadgeFallback() {
  // Used on the server stats grid before the BrowserCleanup card has had
  // a chance to read window storage.
  return <Badge variant="outline">survey on load</Badge>;
}

/**
 * Authenticated YouTube Data API client.
 *
 * Responsibilities
 * ----------------
 * 1. Look up a connected YouTube account row.
 * 2. Refresh its access token if it's expired (or within the 60-second skew
 *    window) using the stored refresh token. New tokens are persisted back
 *    to the row so subsequent requests don't all hit Google for refresh.
 * 3. Wrap `videos.list`, `search.list`, etc. with a typed `fetch` that
 *    automatically retries once on 401 (treating it as "token revoked
 *    silently — try a fresh refresh").
 *
 * We use a single picked-account model (the first YouTube account in the DB).
 * Multi-channel switching can live behind a future user setting.
 */
import "server-only";

import { and, eq } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";
import { getYouTubeConfig } from "@/lib/oauth";

export class YouTubeClientError extends Error {}

type AccountRow = typeof schema.accounts.$inferSelect;

/** Skew window — refresh tokens that expire in under this many ms. */
const EXPIRY_SKEW_MS = 60 * 1000;

/** Pick the first connected YouTube account. Null if none connected. */
function getYouTubeAccount(): AccountRow | null {
  const rows = db
    .select()
    .from(schema.accounts)
    .where(eq(schema.accounts.platform, "youtube"))
    .limit(1)
    .all();
  return rows[0] ?? null;
}

async function refreshAccessToken(account: AccountRow): Promise<string> {
  if (!account.refreshToken) {
    throw new YouTubeClientError(
      `YouTube account "${account.label}" has no refresh token. Disconnect + reconnect from /accounts.`,
    );
  }
  const cfg = getYouTubeConfig();
  if (!cfg.clientId || !cfg.clientSecret) {
    throw new YouTubeClientError(
      "YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET are not set in .env.",
    );
  }

  const resp = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: cfg.clientId,
      client_secret: cfg.clientSecret,
      refresh_token: account.refreshToken,
      grant_type: "refresh_token",
    }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new YouTubeClientError(
      `YouTube token refresh failed (${resp.status}): ${detail.slice(0, 200)}`,
    );
  }
  const data = (await resp.json()) as {
    access_token?: string;
    expires_in?: number;
  };
  if (!data.access_token) {
    throw new YouTubeClientError("YouTube refresh did not return access_token.");
  }
  const expiresAt = data.expires_in
    ? new Date(Date.now() + data.expires_in * 1000)
    : null;

  db.update(schema.accounts)
    .set({
      accessToken: data.access_token,
      expiresAt,
      updatedAt: new Date(),
    })
    .where(eq(schema.accounts.id, account.id))
    .run();

  return data.access_token;
}

async function getValidAccessToken(): Promise<string> {
  const account = getYouTubeAccount();
  if (!account) {
    throw new YouTubeClientError(
      "No YouTube account connected. Visit /accounts and click Connect with YouTube.",
    );
  }
  const expiresAt = account.expiresAt
    ? account.expiresAt instanceof Date
      ? account.expiresAt.getTime()
      : Number(account.expiresAt)
    : 0;
  if (account.accessToken && expiresAt && expiresAt - EXPIRY_SKEW_MS > Date.now()) {
    return account.accessToken;
  }
  return refreshAccessToken(account);
}

/**
 * Fetch from the YouTube Data API with auth. Auto-retries once on 401 with a
 * fresh token (covers the case where the cached token was revoked server-side).
 */
export async function youtubeFetch(
  url: string,
  init?: RequestInit,
): Promise<Response> {
  const apply = async (token: string) =>
    fetch(url, {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        Authorization: `Bearer ${token}`,
      },
      signal: init?.signal ?? AbortSignal.timeout(15_000),
    });

  const token = await getValidAccessToken();
  const first = await apply(token);
  if (first.status !== 401) return first;

  // 401 → token was revoked or just rotated. Force a refresh and retry once.
  const account = getYouTubeAccount();
  if (!account) return first;
  const fresh = await refreshAccessToken(account);
  return apply(fresh);
}

export function hasYouTubeAccount(): boolean {
  return getYouTubeAccount() !== null;
}

// Silence an unused-export warning if drizzle’s helper isn’t referenced
// elsewhere from this file. `and` is here for future use (e.g. filtering by
// region per account in the future).
void and;

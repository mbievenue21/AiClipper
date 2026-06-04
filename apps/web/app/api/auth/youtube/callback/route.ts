/**
 * GET /api/auth/youtube/callback
 *
 * Google redirects the user here after they Allow/Deny on the consent
 * screen. Verify the state cookie, exchange the code for tokens, fetch
 * the channel name for a friendlier label, and upsert into `accounts`.
 *
 * `onConflictDoUpdate` lets the same channel reconnect (refreshing tokens)
 * without us having to delete the row first.
 */
import { sql } from "drizzle-orm";
import { revalidatePath } from "next/cache";

import { db, schema } from "@/lib/db/client";
import {
  consumeOAuthCookies,
  getYouTubeConfig,
  oauthErrorRedirect,
  oauthSuccessRedirect,
} from "@/lib/oauth";

export const dynamic = "force-dynamic";

type GoogleTokenResponse = {
  access_token: string;
  refresh_token?: string;
  expires_in: number;
  token_type: string;
  scope: string;
};

type ChannelListResponse = {
  items?: Array<{ snippet?: { title?: string } }>;
};

export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const stateFromProvider = url.searchParams.get("state");
  const providerError = url.searchParams.get("error");

  if (providerError) {
    return oauthErrorRedirect(`YouTube denied access: ${providerError}`);
  }

  const cookies = await consumeOAuthCookies("youtube");
  if (!code || !stateFromProvider || !cookies.state) {
    return oauthErrorRedirect("YouTube OAuth callback missing code/state.");
  }
  if (stateFromProvider !== cookies.state) {
    return oauthErrorRedirect("YouTube OAuth state mismatch (possible CSRF).");
  }

  const cfg = getYouTubeConfig();
  if (!cfg.clientId || !cfg.clientSecret || !cfg.redirectUri) {
    return oauthErrorRedirect("YouTube OAuth env vars not set.");
  }

  let tokens: GoogleTokenResponse;
  try {
    const tokenResp = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: cfg.clientId,
        client_secret: cfg.clientSecret,
        redirect_uri: cfg.redirectUri,
        grant_type: "authorization_code",
      }),
    });
    if (!tokenResp.ok) {
      const detail = await tokenResp.text().catch(() => "");
      return oauthErrorRedirect(
        `YouTube token exchange failed (${tokenResp.status}): ${detail.slice(0, 200)}`,
      );
    }
    tokens = (await tokenResp.json()) as GoogleTokenResponse;
  } catch (e) {
    return oauthErrorRedirect(
      `YouTube token exchange error: ${(e as Error).message.slice(0, 200)}`,
    );
  }

  if (!tokens.access_token) {
    return oauthErrorRedirect("YouTube returned no access_token.");
  }

  let label = "YouTube channel";
  try {
    const chResp = await fetch(
      "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
      { headers: { Authorization: `Bearer ${tokens.access_token}` } },
    );
    if (chResp.ok) {
      const data = (await chResp.json()) as ChannelListResponse;
      const title = data.items?.[0]?.snippet?.title;
      if (title) label = title;
    }
  } catch {
    // Channel lookup is best-effort; fall back to the generic label.
  }

  const expiresAt = tokens.expires_in
    ? new Date(Date.now() + tokens.expires_in * 1000)
    : null;

  try {
    db.insert(schema.accounts)
      .values({
        platform: "youtube",
        label,
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token ?? null,
        expiresAt,
        rawJson: { scope: tokens.scope, token_type: tokens.token_type },
      })
      .onConflictDoUpdate({
        target: [schema.accounts.platform, schema.accounts.label],
        set: {
          accessToken: tokens.access_token,
          // Google may omit refresh_token on re-consent for the same scope.
          // Keep the existing one in that case rather than nulling it.
          refreshToken: tokens.refresh_token
            ? tokens.refresh_token
            : sql`refresh_token`,
          expiresAt,
          rawJson: { scope: tokens.scope, token_type: tokens.token_type },
          updatedAt: new Date(),
        },
      })
      .run();
  } catch (e) {
    return oauthErrorRedirect(
      `Could not save YouTube account: ${(e as Error).message.slice(0, 200)}`,
    );
  }

  revalidatePath("/accounts");
  return oauthSuccessRedirect("youtube", label);
}

/**
 * GET /api/auth/instagram/callback
 *
 * Instagram redirects here after the user clicks "Allow". We then:
 *   1. Verify the `state` cookie matches the query.
 *   2. POST the code to `api.instagram.com/oauth/access_token` to get a
 *      SHORT-LIVED token (~1 hour).
 *   3. GET `graph.instagram.com/access_token?grant_type=ig_exchange_token`
 *      to upgrade that to a LONG-LIVED token (~60 days). Always do step 3
 *      so the stored token is long-lived; the worker refreshes it when
 *      it's within 7 days of expiry.
 *   4. GET `/me` to fetch username + account_type for the human-readable
 *      label.
 *   5. Upsert the account row.
 */
import { NextRequest } from "next/server";
import { and, eq } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";
import {
  consumeOAuthCookies,
  getInstagramConfig,
  oauthErrorRedirect,
  oauthSuccessRedirect,
} from "@/lib/oauth";

export const dynamic = "force-dynamic";

type ShortLivedResp = {
  access_token: string;
  user_id: number | string;
  permissions?: string[];
};

type LongLivedResp = {
  access_token: string;
  token_type: string;
  expires_in: number; // seconds
};

type MeResp = {
  id: string;
  username: string;
  name?: string;
  account_type?: string; // BUSINESS | MEDIA_CREATOR | PERSONAL
};

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const code = url.searchParams.get("code");
  const stateFromQuery = url.searchParams.get("state");
  const providerError = url.searchParams.get("error");
  const providerReason = url.searchParams.get("error_reason");
  const providerDesc = url.searchParams.get("error_description");

  if (providerError) {
    return oauthErrorRedirect(
      `Instagram refused: ${providerReason || providerError}${
        providerDesc ? ` (${providerDesc})` : ""
      }`,
    );
  }
  if (!code) return oauthErrorRedirect("Instagram returned no code.");

  const { state: stateFromCookie } = await consumeOAuthCookies("instagram");
  if (!stateFromCookie || stateFromCookie !== stateFromQuery) {
    return oauthErrorRedirect("OAuth state mismatch. Try connecting again.");
  }

  const cfg = getInstagramConfig();
  if (!cfg.appId || !cfg.appSecret || !cfg.redirectUri) {
    return oauthErrorRedirect("Instagram is not configured server-side.");
  }

  // 1. Short-lived token exchange.
  const tokenForm = new URLSearchParams({
    client_id: cfg.appId,
    client_secret: cfg.appSecret,
    grant_type: "authorization_code",
    redirect_uri: cfg.redirectUri,
    code,
  });

  const shortRes = await fetch("https://api.instagram.com/oauth/access_token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: tokenForm.toString(),
  });
  if (!shortRes.ok) {
    const text = await shortRes.text();
    return oauthErrorRedirect(
      `Token exchange failed (${shortRes.status}): ${text.slice(0, 300)}`,
    );
  }
  const short = (await shortRes.json()) as ShortLivedResp;
  if (!short.access_token) {
    return oauthErrorRedirect("Instagram returned no access_token.");
  }

  // 2. Upgrade to long-lived (60 days). This is REQUIRED for any practical
  //    use — the short-lived token expires in an hour.
  const longUrl = new URL("https://graph.instagram.com/access_token");
  longUrl.searchParams.set("grant_type", "ig_exchange_token");
  longUrl.searchParams.set("client_secret", cfg.appSecret);
  longUrl.searchParams.set("access_token", short.access_token);

  const longRes = await fetch(longUrl.toString(), { method: "GET" });
  if (!longRes.ok) {
    const text = await longRes.text();
    return oauthErrorRedirect(
      `Long-lived token exchange failed (${longRes.status}): ${text.slice(0, 300)}`,
    );
  }
  const long = (await longRes.json()) as LongLivedResp;
  const accessToken = long.access_token || short.access_token;
  const expiresAt = long.expires_in
    ? new Date(Date.now() + long.expires_in * 1000)
    : null;

  // 3. Identify the connected IG user.
  const meUrl = new URL("https://graph.instagram.com/v23.0/me");
  meUrl.searchParams.set("fields", "id,username,name,account_type");
  meUrl.searchParams.set("access_token", accessToken);
  const meRes = await fetch(meUrl.toString(), { method: "GET" });
  let me: MeResp = { id: String(short.user_id), username: "instagram" };
  if (meRes.ok) {
    try {
      me = (await meRes.json()) as MeResp;
    } catch {
      // keep fallback
    }
  }

  if (
    me.account_type &&
    me.account_type !== "BUSINESS" &&
    me.account_type !== "MEDIA_CREATOR" &&
    me.account_type !== "CREATOR"
  ) {
    return oauthErrorRedirect(
      `@${me.username} is a ${me.account_type} account. Instagram only allows publishing from Business or Creator accounts. Switch in the Instagram app and reconnect.`,
    );
  }

  const label = me.username ? `@${me.username}` : "Instagram account";

  const existing = db
    .select()
    .from(schema.accounts)
    .where(
      and(
        eq(schema.accounts.platform, "instagram"),
        eq(schema.accounts.label, label),
      ),
    )
    .limit(1)
    .all()[0];

  const raw: Record<string, unknown> = {
    ig_user_id: me.id,
    username: me.username,
    name: me.name,
    account_type: me.account_type,
    permissions: short.permissions,
    long_lived_expires_in: long.expires_in,
  };

  if (existing) {
    db.update(schema.accounts)
      .set({
        accessToken,
        // IG Business Login doesn't return a refresh_token; renewal is
        // done via /refresh_access_token using the current long-lived
        // token before it expires.
        refreshToken: null,
        expiresAt,
        rawJson: raw,
      })
      .where(eq(schema.accounts.id, existing.id))
      .run();
  } else {
    db.insert(schema.accounts)
      .values({
        platform: "instagram",
        label,
        accessToken,
        refreshToken: null,
        expiresAt,
        rawJson: raw,
      })
      .run();
  }

  return oauthSuccessRedirect("instagram", label);
}

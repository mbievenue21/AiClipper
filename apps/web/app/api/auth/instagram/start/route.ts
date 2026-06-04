/**
 * GET /api/auth/instagram/start
 *
 * Kicks off Instagram Business Login (the 2024 unified flow that doesn't
 * require an intermediate Facebook Page connection).
 *
 * Docs:
 *   https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login
 *
 * Scopes requested
 * ----------------
 * - `instagram_business_basic`           — read profile (username, account_type, id)
 * - `instagram_business_content_publish` — upload + publish Reels / images
 *
 * Notes
 * -----
 * - The IG account must be Business OR Creator (personal accounts cannot
 *   call the Content Publishing API).
 * - During app development, the IG account must be added as a Tester /
 *   Instagram Tester under the Meta Developer app's Roles tab and the
 *   tester must accept the invite from instagram.com.
 */
import { NextRequest } from "next/server";
import {
  generateState,
  getInstagramConfig,
  oauthErrorRedirect,
  setOAuthCookies,
} from "@/lib/oauth";

export const dynamic = "force-dynamic";

const AUTH_URL = "https://www.instagram.com/oauth/authorize";

const SCOPES = [
  "instagram_business_basic",
  "instagram_business_content_publish",
];

export async function GET(_req: NextRequest): Promise<Response> {
  const cfg = getInstagramConfig();
  if (!cfg.appId || !cfg.appSecret || !cfg.redirectUri) {
    return oauthErrorRedirect(
      "Instagram is not configured. Set INSTAGRAM_APP_ID, INSTAGRAM_APP_SECRET and INSTAGRAM_REDIRECT_URI in .env.",
    );
  }

  const state = generateState();
  await setOAuthCookies("instagram", state, null);

  const url = new URL(AUTH_URL);
  url.searchParams.set("enable_fb_login", "0");
  url.searchParams.set("force_authentication", "1");
  url.searchParams.set("client_id", cfg.appId);
  url.searchParams.set("redirect_uri", cfg.redirectUri);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", SCOPES.join(","));
  url.searchParams.set("state", state);

  return Response.redirect(url.toString(), 302);
}

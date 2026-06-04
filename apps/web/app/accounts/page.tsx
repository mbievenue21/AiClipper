/**
 * /accounts — manage connected YouTube + Instagram credentials.
 *
 * Primary flow: click "Connect with YouTube/Instagram", complete the
 * OAuth consent on the provider, get redirected back here with tokens
 * already saved into the `accounts` table.
 *
 * Fallback flow (collapsed under "Advanced"): paste an access token you
 * obtained out-of-band. Handy for debugging or for self-hosted setups
 * where the OAuth redirect URI can't reach this app.
 */
import { desc } from "drizzle-orm";
import { Suspense } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { db, schema } from "@/lib/db/client";
import {
  instagramOAuthReady,
  missingInstagramKeys,
  missingYouTubeKeys,
  youtubeOAuthReady,
} from "@/lib/oauth";

import { AccountsManager } from "./accounts-manager";
import { ConnectCard } from "./connect-buttons";
import { OAuthToast } from "./oauth-toast";

export const dynamic = "force-dynamic";

export default async function AccountsPage() {
  const accounts = db
    .select({
      id: schema.accounts.id,
      platform: schema.accounts.platform,
      label: schema.accounts.label,
      refreshToken: schema.accounts.refreshToken,
      expiresAt: schema.accounts.expiresAt,
      createdAt: schema.accounts.createdAt,
    })
    .from(schema.accounts)
    .orderBy(desc(schema.accounts.createdAt))
    .all();

  const ytReady = youtubeOAuthReady();
  const igReady = instagramOAuthReady();
  const ytMissing = missingYouTubeKeys();
  const igMissing = missingInstagramKeys();

  const ytCount = accounts.filter((a) => a.platform === "youtube").length;
  const igCount = accounts.filter((a) => a.platform === "instagram").length;

  return (
    <div className="container mx-auto max-w-3xl space-y-6 px-4 py-10">
      <Suspense>
        <OAuthToast />
      </Suspense>

      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Connected accounts
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          The worker uses these tokens to upload clips to YouTube and
          Instagram. Tokens are stored in your local SQLite database — they
          never leave this machine.
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <ConnectCard
          platform="youtube"
          oauthReady={ytReady}
          missingKeys={ytMissing}
          connectedCount={ytCount}
        />
        <ConnectCard
          platform="instagram"
          oauthReady={igReady}
          missingKeys={igMissing}
          connectedCount={igCount}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Connected ({accounts.length})</CardTitle>
          <CardDescription className="text-xs">
            Tokens persist across worker restarts. Remove and reconnect any
            time you rotate credentials.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <AccountsManager
            accounts={accounts.map((a) => ({
              ...a,
              expiresAt:
                a.expiresAt instanceof Date
                  ? a.expiresAt.getTime()
                  : a.expiresAt
                    ? Number(a.expiresAt)
                    : null,
              createdAt:
                a.createdAt instanceof Date
                  ? a.createdAt.getTime()
                  : Number(a.createdAt),
              platform: a.platform as "youtube" | "instagram",
            }))}
          />
        </CardContent>
      </Card>
    </div>
  );
}

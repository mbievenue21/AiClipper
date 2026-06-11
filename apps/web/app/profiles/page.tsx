import Link from "next/link";
import { Plus, Sparkles } from "lucide-react";

import { createProfileAction } from "@/app/profiles/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { listHighlightProfiles } from "@/lib/profiles/queries";

export const dynamic = "force-dynamic";

export default async function ProfilesPage() {
  const profiles = await listHighlightProfiles();

  return (
    <div className="container mx-auto max-w-6xl px-4 py-10">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Highlight Profiles
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Content-specific clipping configs trained from reference shorts and
            editor feedback.
          </p>
        </div>
        <Button asChild variant="outline">
          <Link href="/train">
            <Sparkles className="size-4" />
            Train from video
          </Link>
        </Button>
      </div>

      <div className="mb-8 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Create profile</CardTitle>
            <CardDescription>
              Start a new content profile (e.g. Valorant reaction shorts).
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form
              action={async (formData) => {
                "use server";
                await createProfileAction({
                  name: String(formData.get("name") ?? ""),
                  slug: String(formData.get("slug") ?? ""),
                  description: String(formData.get("description") ?? ""),
                  game: String(formData.get("game") ?? ""),
                  contentType: String(formData.get("contentType") ?? ""),
                });
              }}
              className="space-y-3"
            >
              <div className="space-y-1">
                <label htmlFor="name" className="text-sm font-medium">Name</label>
                <Input id="name" name="name" placeholder="Valorant Reaction Shorts" required />
              </div>
              <div className="space-y-1">
                <label htmlFor="slug" className="text-sm font-medium">Slug</label>
                <Input
                  id="slug"
                  name="slug"
                  placeholder="valorant_reaction_shorts"
                  pattern="[a-z0-9_]+"
                  required
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <label htmlFor="game" className="text-sm font-medium">Game</label>
                  <Input id="game" name="game" placeholder="valorant" />
                </div>
                <div className="space-y-1">
                  <label htmlFor="contentType" className="text-sm font-medium">Content type</label>
                  <Input id="contentType" name="contentType" placeholder="reaction_shorts" />
                </div>
              </div>
              <Button type="submit">
                <Plus className="size-4" />
                Create profile
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card className="border-dashed">
          <CardHeader>
            <CardTitle className="text-base">Quick train</CardTitle>
            <CardDescription>
              Upload reference shorts and submit feedback in one flow from the
              home training page.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild>
              <Link href="/train">Open training studio</Link>
            </Button>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {profiles.map((p) => (
          <Link key={p.id} href={`/profiles/${p.id}`}>
            <Card className="transition-colors hover:bg-muted/40">
              <CardHeader>
                <div className="flex items-center justify-between gap-2">
                  <CardTitle className="text-base">{p.name}</CardTitle>
                  <Badge variant={p.status === "active" ? "default" : "secondary"}>
                    {p.status}
                  </Badge>
                </div>
                <CardDescription>{p.description ?? p.slug}</CardDescription>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                {p.game ? `${p.game} · ` : ""}
                {p.contentType ?? "general"}
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}

import Link from "next/link";
import { desc } from "drizzle-orm";
import { FileVideo, Plus, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { db, schema } from "@/lib/db/client";
import { ProjectCard } from "./projects/project-card";

// Force dynamic rendering; we always want the latest project list.
export const dynamic = "force-dynamic";

export default async function HomePage() {
  const projects = await db
    .select()
    .from(schema.projects)
    .orderBy(desc(schema.projects.createdAt))
    .limit(50);

  return (
    <div className="container mx-auto max-w-6xl px-4 py-10">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Paste a YouTube or Twitch VOD URL to start extracting highlights.
          </p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline">
            <Link href="/train">
              <Sparkles className="size-4" />
              Train profile
            </Link>
          </Button>
          <Button asChild>
            <Link href="/projects/new">
              <Plus className="size-4" />
              New project
            </Link>
          </Button>
        </div>
      </div>

      {projects.length === 0 ? (
        <Card className="border-dashed">
          <CardHeader className="items-center text-center">
            <div className="mb-2 flex size-12 items-center justify-center rounded-full bg-muted">
              <FileVideo className="size-6 text-muted-foreground" />
            </div>
            <CardTitle>No projects yet</CardTitle>
            <CardDescription className="max-w-sm">
              Create your first project to download a long-form video and let
              the worker analyze it for highlight clips.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center pb-8">
            <Button asChild>
              <Link href="/projects/new">
                <Sparkles className="size-4" />
                Create your first project
              </Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <ProjectCard
              key={p.id}
              id={p.id}
              name={p.name}
              status={p.status}
              sourceUrl={p.sourceUrl}
              createdAt={p.createdAt}
            />
          ))}
        </div>
      )}
    </div>
  );
}

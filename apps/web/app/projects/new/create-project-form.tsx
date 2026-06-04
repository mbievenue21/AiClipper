"use client";

import { useActionState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";

import { createProject, type CreateProjectState } from "./actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const initial: CreateProjectState = {};

export function CreateProjectForm() {
  const [state, action, pending] = useActionState(createProject, initial);

  return (
    <Card className="mx-auto max-w-lg">
      <CardHeader>
        <CardTitle>New project</CardTitle>
        <CardDescription>
          Paste a public YouTube or Twitch VOD URL. The worker will download it
          with yt-dlp into <code className="text-xs">data/videos/</code>.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form action={action} className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="sourceUrl" className="text-sm font-medium">
              Source URL
            </label>
            <Input
              id="sourceUrl"
              name="sourceUrl"
              type="url"
              placeholder="https://www.youtube.com/watch?v=..."
              required
              disabled={pending}
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="name" className="text-sm font-medium">
              Name <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <Input
              id="name"
              name="name"
              placeholder="Auto-generated from URL if empty"
              disabled={pending}
            />
          </div>
          {state.error && (
            <p className="text-sm text-destructive" role="alert">
              {state.error}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="submit" disabled={pending}>
              {pending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Starting download…
                </>
              ) : (
                "Download & create"
              )}
            </Button>
            <Button variant="outline" asChild disabled={pending}>
              <Link href="/">Cancel</Link>
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

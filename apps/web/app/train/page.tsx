import Link from "next/link";

import { TrainStudio } from "@/app/train/train-studio";
import { Button } from "@/components/ui/button";
import {
  getProfileFeedbackExamples,
  listHighlightProfiles,
} from "@/lib/profiles/queries";
import type { TrainingExample } from "@/lib/db/schema";

export const dynamic = "force-dynamic";

export default async function TrainPage() {
  const profiles = await listHighlightProfiles();
  const feedbackByProfile: Record<string, TrainingExample[]> = {};
  for (const profile of profiles) {
    feedbackByProfile[profile.id] = await getProfileFeedbackExamples(profile.id);
  }

  return (
    <div className="container mx-auto max-w-4xl px-4 py-10">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Profile Training Studio
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Upload reference shorts or paste YouTube / Twitch links. Label each
            example and optimize a highlight profile without leaving the app.
          </p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline">
            <Link href="/profiles">All profiles</Link>
          </Button>
          {profiles[0] && (
            <Button asChild variant="secondary">
              <Link href={`/profiles/${profiles[0].id}`}>Training board</Link>
            </Button>
          )}
        </div>
      </div>

      {profiles.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No profiles yet.{" "}
          <Link href="/profiles" className="underline">
            Create one first
          </Link>
          .
        </p>
      ) : (
        <TrainStudio profiles={profiles} feedbackByProfile={feedbackByProfile} />
      )}
    </div>
  );
}

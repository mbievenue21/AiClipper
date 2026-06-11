import Link from "next/link";
import { notFound } from "next/navigation";

import {
  createDatasetAction,
  retrainFromFeedbackAction,
  startProfileTrainingAction,
} from "@/app/profiles/actions";
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
import {
  getHighlightProfile,
  getProfileVersions,
  getReferenceClips,
  getTrainingRuns,
  listTrainingDatasets,
} from "@/lib/profiles/queries";
import { ProfileTrainingBoard } from "./profile-training-board";

export const dynamic = "force-dynamic";

export default async function ProfileDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const profile = await getHighlightProfile(id);
  if (!profile) notFound();

  const [versions, datasets, runs] = await Promise.all([
    getProfileVersions(profile.id),
    listTrainingDatasets(profile.id),
    getTrainingRuns(profile.id),
  ]);

  const primaryDataset = datasets[0];
  const referenceClips = primaryDataset
    ? await getReferenceClips(primaryDataset.id)
    : [];

  const activeVersion = versions.find((v) => v.isActive) ?? versions[0];

  return (
    <div className="container mx-auto max-w-6xl px-4 py-10">
      <div className="mb-6">
        <Link href="/profiles" className="text-sm text-muted-foreground hover:underline">
          ← Profiles
        </Link>
        <h1 className="mt-2 text-2xl font-semibold">{profile.name}</h1>
        <p className="text-sm text-muted-foreground">{profile.description}</p>
      </div>

      <div className="mb-8 grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Status</CardDescription>
            <CardTitle className="text-lg capitalize">{profile.status}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Live config revision</CardDescription>
            <CardTitle className="text-lg">
              #{activeVersion?.metricsJson?.trainingRevision ?? activeVersion?.versionNumber ?? "—"}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Reference clips</CardDescription>
            <CardTitle className="text-lg">{referenceClips.length}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      <ProfileTrainingBoard
        profileId={profile.id}
        versions={versions}
        runs={runs}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Live config</CardTitle>
            <CardDescription>
              All training updates this single config in place (keywords,
              weights, ranker). New projects always use the latest revision.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {activeVersion ? (
              <div className="rounded-md border p-3 text-sm">
                <div className="font-medium">
                  Revision{" "}
                  {activeVersion.metricsJson?.trainingRevision ??
                    activeVersion.versionNumber}
                  <Badge className="ml-2">live</Badge>
                </div>
                {activeVersion.metricsJson && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    recall@K:{" "}
                    {((activeVersion.metricsJson.recallAtK ?? 0) * 100).toFixed(0)}% · sep:{" "}
                    {(activeVersion.metricsJson.separation ?? 0).toFixed(2)} ·{" "}
                    {activeVersion.metricsJson.trialCount ?? 0} trials
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No config yet — submit training from Train or feedback on a project.
              </p>
            )}
            {versions.length > 1 && (
              <p className="text-xs text-muted-foreground">
                Older snapshots (v1, v2…) from before this change are kept in the
                database but ignored. Only the live revision above is used for scoring.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Training</CardTitle>
            <CardDescription>Datasets, runs, and retrain actions</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <form
              action={async (formData) => {
                "use server";
                await createDatasetAction({
                  profileId: profile.id,
                  name: String(formData.get("datasetName") ?? "New dataset"),
                });
              }}
              className="flex gap-2"
            >
              <Input name="datasetName" placeholder="Dataset name" required />
              <Button type="submit" variant="outline">
                Add dataset
              </Button>
            </form>

            {primaryDataset && (
              <div className="flex flex-wrap gap-2">
                <form
                  action={async () => {
                    "use server";
                    await startProfileTrainingAction({
                      profileId: profile.id,
                      datasetId: primaryDataset.id,
                    });
                  }}
                >
                  <Button type="submit">Start training</Button>
                </form>
                <form
                  action={async () => {
                    "use server";
                    await retrainFromFeedbackAction({
                      profileId: profile.id,
                      datasetId: primaryDataset.id,
                    });
                  }}
                >
                  <Button type="submit" variant="secondary">
                    Retrain from feedback
                  </Button>
                </form>
                <Button asChild variant="outline">
                  <Link href="/train">Upload reference clips</Link>
                </Button>
              </div>
            )}

            <div className="space-y-2">
              {runs.map((run) => (
                <div key={run.id} className="rounded-md border p-3 text-sm">
                  <div className="flex justify-between">
                    <span className="font-medium capitalize">{run.status}</span>
                    <span className="text-xs text-muted-foreground">
                      {run.optimizer}
                    </span>
                  </div>
                  {run.metricsJson && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      trials: {run.metricsJson.trialCount ?? "—"} · objective:{" "}
                      {run.metricsJson.bestObjective?.toFixed(3) ?? "—"}
                    </p>
                  )}
                </div>
              ))}
              {runs.length === 0 && (
                <p className="text-sm text-muted-foreground">No training runs yet.</p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {activeVersion?.configJson && (
        <details className="mt-6 rounded-lg border p-4">
          <summary className="cursor-pointer text-sm font-medium">
            Raw config JSON (advanced)
          </summary>
          <pre className="mt-3 max-h-96 overflow-auto rounded-md bg-muted p-4 text-xs">
            {JSON.stringify(activeVersion.configJson, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

import { getRankingPreferences } from "@/lib/ranking/preferences";
import { listHighlightProfiles } from "@/lib/profiles/queries";

import { CreateProjectForm } from "./create-project-form";

export default async function NewProjectPage() {
  const [prefs, profiles] = await Promise.all([
    getRankingPreferences(),
    listHighlightProfiles(),
  ]);
  return (
    <div className="container mx-auto max-w-6xl px-4 py-10">
      <CreateProjectForm
        defaultPreRollSeconds={prefs.learnedPreRollSeconds}
        defaultTailPaddingSeconds={prefs.learnedTailPaddingSeconds}
        feedbackCount={prefs.feedbackCount}
        profiles={profiles}
      />
    </div>
  );
}

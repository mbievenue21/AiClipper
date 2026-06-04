const DEFAULT_WORKER_URL = "http://127.0.0.1:8000";

export function getWorkerUrl(): string {
  return process.env.WORKER_URL ?? DEFAULT_WORKER_URL;
}

export type WorkerJob = {
  id: string;
  type: string;
  status: string;
  progress: number;
  progress_message: string | null;
  project_id: string | null;
  error_message: string | null;
};

export async function enqueueIngestJob(
  projectId: string,
  url: string,
): Promise<WorkerJob> {
  const res = await fetch(`${getWorkerUrl()}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "ingest",
      project_id: projectId,
      payload: { project_id: projectId, url },
    }),
    cache: "no-store",
  });

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `Worker rejected ingest job (${res.status}). Is the worker running on ${getWorkerUrl()}? ${detail}`,
    );
  }

  return res.json() as Promise<WorkerJob>;
}

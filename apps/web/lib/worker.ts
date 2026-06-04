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
  // Hard timeout — if the worker is down or restarting, fail fast instead of
  // letting the server action hang for minutes and leave the project orphaned.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);

  let res: Response;
  try {
    res = await fetch(`${getWorkerUrl()}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "ingest",
        project_id: projectId,
        payload: { project_id: projectId, url },
      }),
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    throw new Error(
      `Could not reach the worker at ${getWorkerUrl()}: ${reason}. Is \`pnpm dev\` running?`,
    );
  } finally {
    clearTimeout(timeout);
  }

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `Worker rejected ingest job (${res.status}). Is the worker running on ${getWorkerUrl()}? ${detail}`,
    );
  }

  return res.json() as Promise<WorkerJob>;
}

export type WorkerHealth = {
  ok: boolean;
  url: string;
  status?: number;
  body?: unknown;
  error?: string;
  latencyMs?: number;
};

export async function pingWorker(timeoutMs = 2500): Promise<WorkerHealth> {
  const url = getWorkerUrl();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const started = Date.now();
  try {
    const res = await fetch(`${url}/health`, {
      cache: "no-store",
      signal: controller.signal,
    });
    const body = await res.json().catch(() => null);
    return {
      ok: res.ok,
      url,
      status: res.status,
      body,
      latencyMs: Date.now() - started,
    };
  } catch (err) {
    return {
      ok: false,
      url,
      error: err instanceof Error ? err.message : String(err),
      latencyMs: Date.now() - started,
    };
  } finally {
    clearTimeout(timeout);
  }
}

export type WorkerStats = {
  jobs_by_status: Record<string, number>;
  projects_by_status: Record<string, number>;
  oldest_pending_age_s: number | null;
  oldest_running_age_s: number | null;
};

export async function fetchWorkerStats(timeoutMs = 3000): Promise<WorkerStats | null> {
  const url = getWorkerUrl();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${url}/admin/stats`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) return null;
    return (await res.json()) as WorkerStats;
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

export async function callWorkerAdmin(
  path: "heal" | "cancel-pending",
  timeoutMs = 5000,
): Promise<{
  jobs_reset: number;
  projects_healed: number;
  pending_cancelled: number;
}> {
  const url = getWorkerUrl();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${url}/admin/${path}`, {
      method: "POST",
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(
        `Worker returned ${res.status} from /admin/${path}: ${await res.text()}`,
      );
    }
    return await res.json();
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    throw new Error(
      `Could not reach the worker at ${url}: ${reason}. Is \`pnpm dev\` running?`,
    );
  } finally {
    clearTimeout(timeout);
  }
}

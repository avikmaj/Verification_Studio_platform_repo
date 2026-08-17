/**
 * Typed client for the UVM Verification Studio API.
 *
 * Read routes are unauthenticated; execution routes need a bearer token that
 * the user supplies at runtime and that is held in memory only. It is never
 * written to localStorage — a token that runs a simulator is a remote code
 * execution credential, and persisting it in the browser is not a trade worth
 * making for the convenience of not retyping it.
 */

const BUILD_TIME_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
const STORAGE_KEY = "uvmstudio.apiBase";

/**
 * Resolved API base URL.
 *
 * Precedence: ?api= query param > localStorage > build-time env.
 *
 * Runtime configurability is deliberate: the dashboard must be deployable
 * before the backend exists, and one frontend must be able to point at a
 * laptop, a staging box or production without a rebuild. A URL is not a
 * credential, so persisting it is fine — the bearer token is not persisted.
 */
export function getApiBase(): string {
  if (typeof window === "undefined") return BUILD_TIME_BASE;
  const q = new URLSearchParams(window.location.search).get("api");
  if (q) {
    window.localStorage.setItem(STORAGE_KEY, q.replace(/\/$/, ""));
    return q.replace(/\/$/, "");
  }
  return (
    window.localStorage.getItem(STORAGE_KEY)?.replace(/\/$/, "") ||
    BUILD_TIME_BASE
  );
}

export function setApiBase(url: string): void {
  if (typeof window === "undefined") return;
  const clean = url.trim().replace(/\/$/, "");
  if (clean) window.localStorage.setItem(STORAGE_KEY, clean);
  else window.localStorage.removeItem(STORAGE_KEY);
}

/** @deprecated for display only — call getApiBase() when making requests. */
export const API_BASE = BUILD_TIME_BASE;

export type RunStatus =
  | "PASS"
  | "FAIL"
  | "NOT_VERIFIED"
  | "BLOCKED"
  | "ERROR";

export interface EnvInfo {
  platform: Record<string, unknown>;
  frontends: Record<string, { version: string; capabilities: Record<string, string> }>;
  simulators: Record<
    string,
    {
      available: boolean;
      version?: string | null;
      exec_host?: string;
      solver?: boolean | null;
      capabilities?: Record<string, string>;
      error?: string;
    }
  >;
  uvm: { version: string; version_string: string; home: string } | null;
  workspace: string;
  execution_enabled: boolean;
  disk_free_mb: number | null;
}

export interface TestSpec {
  name: string;
  tier: string;
  seeds: number;
  expect: string;
  tags: string[];
}

export interface ProjectInfo {
  name: string;
  dir: string;
  top?: string;
  language_standard?: string;
  backend?: string;
  tests?: TestSpec[];
  error?: string;
}

export interface RegressionRow {
  id: number;
  name: string;
  tier: string;
  status: string;
  total: number;
  passed: number;
  failed: number;
  not_verified: number;
  blocked: number;
  started_utc: string;
  finished_utc: string | null;
  backend: string | null;
  backend_version: string | null;
  uvm_version: string | null;
  git_commit: string | null;
  git_branch: string | null;
  git_dirty: number | null;
}

export interface Cluster {
  signature: string;
  occurrences: number;
  triage_state: string;
  first_seen_utc: string;
  last_seen_utc: string;
}

export interface Report {
  regression: RegressionRow;
  summary: {
    total: number;
    passed: number;
    failed: number;
    not_verified: number;
    blocked: number;
    status: string;
    pass_rate: number;
  };
  tests: Array<{
    test: string;
    tier: string;
    seeds: number[];
    statuses: Record<string, number>;
    failures: Array<{
      seed: number;
      status: string;
      signature: string | null;
      reasons: string[];
      log: string | null;
      waves: string | null;
      repro: string | null;
    }>;
  }>;
  failure_clusters: Cluster[];
}

export interface CoverageSummary {
  functional: { covered: number; total: number; percent: number };
  by_kind: Record<string, { covered: number; total: number; percent: number }>;
  holes: Array<{
    name: string;
    kind: string;
    file: string;
    line: number;
    hierarchy: string;
  }>;
  databases?: number;
  sources: string[];
}

export interface Job {
  id: string;
  kind: string;
  project: string;
  params: Record<string, unknown>;
  state: "QUEUED" | "RUNNING" | "DONE" | "FAILED" | "CANCELLED";
  status: RunStatus;
  created: number;
  started: number | null;
  finished: number | null;
  duration_s: number | null;
  result: Record<string, any> | null;
  error: string | null;
}

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function req<T>(
  path: string,
  init?: RequestInit & { token?: string }
): Promise<T> {
  const base = getApiBase();
  if (!base) {
    throw new ApiError(
      "No API URL configured. Set it in the header field, append ?api=https://… " +
        "to the URL, or build with NEXT_PUBLIC_API_URL.",
      0
    );
  }
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init?.token) headers.Authorization = `Bearer ${init.token}`;
  if (init?.body) headers["Content-Type"] = "application/json";

  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string>) },
    cache: "no-store",
  });
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try {
      detail = JSON.parse(text).detail ?? text;
    } catch {
      /* plain text body */
    }
    throw new ApiError(detail || res.statusText, res.status);
  }
  const ctype = res.headers.get("content-type") ?? "";
  return (ctype.includes("json") ? JSON.parse(text) : (text as unknown)) as T;
}

export const api = {
  env: () => req<EnvInfo>("/env"),
  projects: () => req<{ workspace: string; projects: ProjectInfo[] }>("/projects"),

  regressions: (p: string) =>
    req<{
      history: RegressionRow[];
      clusters: Cluster[];
      seed_effectiveness: Array<{ seed: number; unique_failures: number; runs: number }>;
    }>(`/projects/${encodeURIComponent(p)}/regressions`),

  report: (p: string, id: number) =>
    req<Report>(`/projects/${encodeURIComponent(p)}/regressions/${id}`),

  coverage: (p: string) =>
    req<CoverageSummary>(`/projects/${encodeURIComponent(p)}/coverage`),

  lint: (p: string) =>
    req<{
      frontend_ok: boolean;
      diagnostics: { counts: Record<string, number>; items: any[] };
      findings: any[];
      rules: any[];
    }>(`/projects/${encodeURIComponent(p)}/lint`),

  design: (p: string) =>
    req<{ ok: boolean; frontend: string; design: any; diagnostics: any }>(
      `/projects/${encodeURIComponent(p)}/design`
    ),

  logText: (p: string, path: string, tail = 400) =>
    req<string>(
      `/projects/${encodeURIComponent(p)}/runs/${path}?tail=${tail}`
    ),

  jobs: () => req<{ jobs: Job[] }>("/jobs"),
  job: (id: string) => req<Job>(`/jobs/${id}`),
  jobLog: (id: string, tail = 400) => req<string>(`/jobs/${id}/log?tail=${tail}`),

  submit: (token: string, body: Record<string, unknown>) =>
    req<Job>("/jobs", { method: "POST", token, body: JSON.stringify(body) }),
};

/** Status colours. Kept in one place so every surface agrees. */
export const STATUS_COLOR: Record<string, string> = {
  PASS: "var(--pass)",
  FAIL: "var(--fail)",
  NOT_VERIFIED: "var(--nv)",
  BLOCKED: "var(--blocked)",
  ERROR: "var(--error)",
  RUNNING: "var(--info)",
  QUEUED: "var(--muted)",
  DONE: "var(--pass)",
  CANCELLED: "var(--muted)",
};

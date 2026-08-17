"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  getApiBase,
  setApiBase,
  type CoverageSummary,
  type EnvInfo,
  type Job,
  type ProjectInfo,
  type RegressionRow,
  type Report,
} from "@/lib/api";
import {
  Badge,
  Empty,
  ErrorBox,
  Meter,
  Panel,
  StatusStrip,
  Tile,
} from "@/components/ui";

type Tab = "overview" | "regressions" | "coverage" | "lint" | "run";

export default function Page() {
  const [tab, setTab] = useState<Tab>("overview");
  const [env, setEnv] = useState<EnvInfo | null>(null);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [project, setProject] = useState<string>("");
  const [error, setError] = useState<unknown>(null);
  const [apiUrl, setApiUrl] = useState("");
  const [connected, setConnected] = useState(false);

  const connect = useCallback(async () => {
    setError(null);
    try {
      const [e, p] = await Promise.all([api.env(), api.projects()]);
      setEnv(e);
      setProjects(p.projects);
      setConnected(true);
      setProject((cur) => cur || (p.projects[0]?.dir ?? ""));
    } catch (err) {
      setConnected(false);
      setError(err);
    }
  }, []);

  useEffect(() => {
    setApiUrl(getApiBase());
    if (getApiBase()) void connect();
  }, [connect]);

  const current = projects.find((p) => p.dir === project);

  return (
    <main className="wrap">
      <header className="top">
        <h1>UVM Verification Studio</h1>
        <span
          className="sub mono"
          title={connected ? "connected" : "not connected"}
          style={{ color: connected ? "var(--pass)" : "var(--nv)" }}
        >
          {connected ? "\u25CF connected" : "\u25CB not connected"}
        </span>
        <span style={{ flex: 1 }} />
        <input
          style={{ width: 300 }}
          value={apiUrl}
          onChange={(e) => setApiUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              setApiBase(apiUrl);
              void connect();
            }
          }}
          placeholder="https://your-api.up.railway.app"
          aria-label="API base URL"
        />
        <button
          className="action"
          onClick={() => {
            setApiBase(apiUrl);
            void connect();
          }}
        >
          Connect
        </button>
        {projects.length > 0 && (
          <select value={project} onChange={(e) => setProject(e.target.value)}>
            {projects.map((p) => (
              <option key={p.dir} value={p.dir}>
                {p.name}
              </option>
            ))}
          </select>
        )}
      </header>

      <nav className="tabs">
        {(
          [
            ["overview", "Overview"],
            ["regressions", "Regressions"],
            ["coverage", "Coverage"],
            ["lint", "Lint"],
            ["run", "Run"],
          ] as [Tab, string][]
        ).map(([id, label]) => (
          <button
            key={id}
            aria-selected={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      <ErrorBox error={error} />

      {tab === "overview" && <Overview env={env} project={current} />}
      {tab === "regressions" && project && <Regressions project={project} />}
      {tab === "coverage" && project && <Coverage project={project} />}
      {tab === "lint" && project && <Lint project={project} />}
      {tab === "run" && project && (
        <Run project={project} executionEnabled={env?.execution_enabled ?? false} />
      )}

      <p className="note">
        <strong>NOT_VERIFIED</strong> means no simulator evidence was observed. It
        is never counted as PASS, and a regression containing one never reports
        PASS.
      </p>
    </main>
  );
}

/* ------------------------------------------------------------------ */
function Overview({
  env,
  project,
}: {
  env: EnvInfo | null;
  project?: ProjectInfo;
}) {
  if (!env) return <Empty>loading environment…</Empty>;

  const sims = Object.entries(env.simulators);
  return (
    <>
      <Panel title="Deployment">
        <div className="tiles">
          <Tile
            n={env.execution_enabled ? "ON" : "OFF"}
            label="Execution"
            color={env.execution_enabled ? "var(--pass)" : "var(--blocked)"}
          />
          <Tile n={env.uvm?.version ?? "—"} label="UVM" />
          <Tile
            n={env.disk_free_mb ? `${(env.disk_free_mb / 1024).toFixed(1)}G` : "—"}
            label="Disk free"
          />
          <Tile n={String(env.platform?.os ?? "—")} label="Host OS" />
        </div>
        {env.uvm && (
          <p className="small mono muted" style={{ marginTop: 12 }}>
            {env.uvm.version_string} — {env.uvm.home}
          </p>
        )}
      </Panel>

      <Panel title="Toolchain">
        <table>
          <thead>
            <tr>
              <th>Component</th>
              <th>Version</th>
              <th>Exec host</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(env.frontends).map(([n, f]) => (
              <tr key={n}>
                <td>
                  <code>{n}</code> <span className="muted small">frontend</span>
                </td>
                <td className="mono">{f.version}</td>
                <td className="muted">local</td>
                <td className="muted small">
                  {Object.values(f.capabilities).filter((v) => v === "SUPPORTED")
                    .length}{" "}
                  supported capabilities
                </td>
              </tr>
            ))}
            {sims.map(([n, s]) => (
              <tr key={n}>
                <td>
                  <code>{n}</code> <span className="muted small">simulator</span>
                </td>
                <td className="mono">
                  {s.available ? s.version : <Badge status="BLOCKED" />}
                </td>
                <td className="muted">{s.exec_host ?? "—"}</td>
                <td className="muted small">
                  {s.error
                    ? s.error
                    : s.solver === false
                    ? "solver MISSING — constraints will fail"
                    : s.solver
                    ? "solver: z3"
                    : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      {project && (
        <Panel title={`Project — ${project.name}`}>
          <div className="row small" style={{ gap: 22, marginBottom: 12 }}>
            <span>
              top <code>{project.top}</code>
            </span>
            <span>
              standard <code>{project.language_standard}</code>
            </span>
            <span>
              backend <code>{project.backend}</code>
            </span>
          </div>
          <table>
            <thead>
              <tr>
                <th>Test</th>
                <th>Tier</th>
                <th className="num">Seeds</th>
                <th>Expect</th>
                <th>Tags</th>
              </tr>
            </thead>
            <tbody>
              {(project.tests ?? []).map((t) => (
                <tr key={t.name}>
                  <td>
                    <code>{t.name}</code>
                  </td>
                  <td>{t.tier}</td>
                  <td className="num">{t.seeds}</td>
                  <td>
                    {t.expect === "FAIL" ? (
                      <span
                        className="badge"
                        style={{ color: "var(--nv)" }}
                        title="negative test: PASS means the violation was detected"
                      >
                        NEGATIVE
                      </span>
                    ) : (
                      <span className="muted">PASS</span>
                    )}
                  </td>
                  <td className="muted small">{t.tags?.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
function Regressions({ project }: { project: string }) {
  const [rows, setRows] = useState<RegressionRow[]>([]);
  const [clusters, setClusters] = useState<any[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setReport(null);
    api
      .regressions(project)
      .then((d) => {
        setRows(d.history);
        setClusters(d.clusters);
        if (d.history.length) {
          api.report(project, d.history[0].id).then(setReport).catch(setError);
        }
      })
      .catch(setError);
  }, [project]);

  return (
    <>
      <ErrorBox error={error} />
      <Panel title="History">
        {rows.length === 0 ? (
          <Empty>no regressions recorded yet — run one from the Run tab</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th className="num">ID</th>
                <th>Status</th>
                <th>Tier</th>
                <th>Result</th>
                <th>Simulator</th>
                <th>Git</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  style={{ cursor: "pointer" }}
                  onClick={() =>
                    api.report(project, r.id).then(setReport).catch(setError)
                  }
                >
                  <td className="num mono">{r.id}</td>
                  <td>
                    <Badge status={r.status} />
                  </td>
                  <td>{r.tier}</td>
                  <td>
                    <StatusStrip
                      counts={{
                        PASS: r.passed,
                        FAIL: r.failed,
                        NOT_VERIFIED: r.not_verified,
                        BLOCKED: r.blocked,
                      }}
                    />
                  </td>
                  <td className="mono small">
                    {r.backend} {r.backend_version}
                  </td>
                  <td className="mono small muted">
                    {(r.git_commit ?? "—").slice(0, 8)}
                    {r.git_dirty ? " ✱" : ""}
                  </td>
                  <td className="muted small">{r.started_utc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {report && <ReportView project={project} report={report} />}

      {clusters.length > 0 && (
        <Panel title="Failure clusters">
          <table>
            <thead>
              <tr>
                <th className="num">Count</th>
                <th>Triage</th>
                <th>Signature</th>
              </tr>
            </thead>
            <tbody>
              {clusters.map((c) => (
                <tr key={c.signature}>
                  <td className="num mono">{c.occurrences}</td>
                  <td className="muted small">{c.triage_state}</td>
                  <td className="mono small">{c.signature}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            Signatures are normalised (hex, times, numbers and paths removed) so
            the same defect clusters across seeds.
          </p>
        </Panel>
      )}
    </>
  );
}

function ReportView({
  project,
  report,
}: {
  project: string;
  report: Report;
}) {
  const s = report.summary;
  const [log, setLog] = useState<{ path: string; text: string } | null>(null);

  return (
    <Panel
      title={`Regression ${report.regression.id} — ${report.regression.name}`}
      aside={<Badge status={s.status} />}
    >
      <div className="tiles" style={{ marginBottom: 16 }}>
        <Tile n={s.passed} label="Pass" color="var(--pass)" />
        <Tile n={s.failed} label="Fail" color="var(--fail)" />
        <Tile n={s.not_verified} label="Not verified" color="var(--nv)" />
        <Tile n={s.blocked} label="Blocked" color="var(--blocked)" />
        <Tile n={s.total} label="Total runs" />
      </div>

      <table>
        <thead>
          <tr>
            <th>Test</th>
            <th>Tier</th>
            <th className="num">Seeds</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {report.tests.map((t) => (
            <tr key={t.test}>
              <td>
                <code>{t.test}</code>
              </td>
              <td>{t.tier}</td>
              <td className="num">{t.seeds.length}</td>
              <td>
                <StatusStrip counts={t.statuses} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {report.tests.some((t) => t.failures.length > 0) && (
        <>
          <h2 style={{ marginTop: 22 }}>Failures</h2>
          <table>
            <thead>
              <tr>
                <th>Test</th>
                <th className="num">Seed</th>
                <th>Status</th>
                <th>Reasons</th>
                <th>Artifacts</th>
              </tr>
            </thead>
            <tbody>
              {report.tests.flatMap((t) =>
                t.failures.map((f) => (
                  <tr key={`${t.test}-${f.seed}`}>
                    <td>
                      <code>{t.test}</code>
                    </td>
                    <td className="num mono">{f.seed}</td>
                    <td>
                      <Badge status={f.status} />
                    </td>
                    <td className="small">{f.reasons.join("; ")}</td>
                    <td className="small">
                      {f.log && (
                        <button
                          className="action"
                          style={{ padding: "2px 8px" }}
                          onClick={async () => {
                            const rel = f.log!.split("/results/").pop();
                            const text = await api.logText(
                              project,
                              `results/${rel}`,
                              300
                            );
                            setLog({ path: f.log!, text });
                          }}
                        >
                          log
                        </button>
                      )}
                      {f.waves && <span className="muted"> waves</span>}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </>
      )}

      {log && (
        <div style={{ marginTop: 16 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="mono small muted">{log.path}</span>
            <button className="action" onClick={() => setLog(null)}>
              close
            </button>
          </div>
          <pre className="log" style={{ marginTop: 8 }}>
            {log.text}
          </pre>
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
function Coverage({ project }: { project: string }) {
  const [cov, setCov] = useState<CoverageSummary | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setCov(null);
    setError(null);
    api.coverage(project).then(setCov).catch(setError);
  }, [project]);

  if (error) return <ErrorBox error={error} />;
  if (!cov) return <Empty>loading coverage…</Empty>;

  return (
    <>
      <Panel
        title="Functional coverage"
        aside={
          <span className="small muted">
            merged from {cov.databases ?? cov.sources.length} database(s)
          </span>
        }
      >
        <Meter percent={cov.functional.percent} />
        <p className="small muted" style={{ marginTop: 8 }}>
          {cov.functional.covered} of {cov.functional.total} covergroup bins hit.
          Line, branch and expression coverage are <em>code</em> coverage and are
          reported separately — they never count toward functional closure.
        </p>
      </Panel>

      <Panel title="By kind">
        <table>
          <thead>
            <tr>
              <th>Kind</th>
              <th className="num">Covered</th>
              <th className="num">Total</th>
              <th>Percent</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(cov.by_kind).map(([k, v]) => (
              <tr key={k}>
                <td>
                  <code>{k}</code>
                </td>
                <td className="num">{v.covered}</td>
                <td className="num">{v.total}</td>
                <td>
                  <Meter percent={v.percent} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <Panel title={`Functional holes (${cov.holes.length})`}>
        {cov.holes.length === 0 ? (
          <Empty>no uncovered covergroup bins</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Bin</th>
                <th>Location</th>
              </tr>
            </thead>
            <tbody>
              {cov.holes.map((h, i) => (
                <tr key={i}>
                  <td className="mono small">{h.name}</td>
                  <td className="muted small mono">
                    {h.file ? `${h.file.split("/").pop()}:${h.line}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}

/* ------------------------------------------------------------------ */
function Lint({ project }: { project: string }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api.lint(project).then(setData).catch(setError);
  }, [project]);

  if (error) return <ErrorBox error={error} />;
  if (!data) return <Empty>compiling and linting…</Empty>;

  const implemented = data.rules.filter((r: any) => r.implemented).length;

  return (
    <>
      <Panel title="Findings" aside={
        <span className="small muted">
          {implemented} of {data.rules.length} rules implemented
        </span>
      }>
        {data.findings.length === 0 ? (
          <Empty>no findings in project-owned code</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Rule</th>
                <th>Severity</th>
                <th>Message</th>
                <th>Location</th>
              </tr>
            </thead>
            <tbody>
              {data.findings.map((f: any, i: number) => (
                <tr key={i}>
                  <td>
                    <code>{f.rule}</code>
                  </td>
                  <td className="small">{f.severity}</td>
                  <td className="small">{f.message}</td>
                  <td className="muted small mono">
                    {f.location
                      ? `${f.location.file.split("/").pop()}:${f.location.line}`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="note">
          Third-party sources (UVM above all) are excluded by default — findings
          you cannot act on are noise.
        </p>
      </Panel>

      <Panel title="Rule catalogue">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Layer</th>
              <th>Status</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {data.rules.map((r: any) => (
              <tr key={r.id} style={{ opacity: r.implemented ? 1 : 0.55 }}>
                <td>
                  <code>{r.id}</code>
                </td>
                <td className="small">{r.layer}</td>
                <td className="small mono">{r.status}</td>
                <td className="small">{r.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </>
  );
}

/* ------------------------------------------------------------------ */
function Run({
  project,
  executionEnabled,
}: {
  project: string;
  executionEnabled: boolean;
}) {
  const [token, setToken] = useState("");
  const [kind, setKind] = useState("regress");
  const [tier, setTier] = useState("L1");
  const [seed, setSeed] = useState("1");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const [error, setError] = useState<unknown>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const d = await api.jobs();
      setJobs(d.jobs);
      if (active) {
        const [j, l] = await Promise.all([
          api.job(active),
          api.jobLog(active, 400),
        ]);
        setLog(l);
        if (["DONE", "FAILED", "CANCELLED"].includes(j.state) && timer.current) {
          clearInterval(timer.current);
          timer.current = null;
        }
      }
    } catch (err) {
      setError(err);
    }
  }, [active]);

  useEffect(() => {
    refresh();
    timer.current = setInterval(refresh, 3000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [refresh]);

  async function submit() {
    setError(null);
    try {
      const job = await api.submit(token, {
        project,
        kind,
        tier,
        seed: seed ? Number(seed) : null,
        jobs: 2,
      });
      setActive(job.id);
      setLog("");
      refresh();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <>
      <ErrorBox error={error} />
      <Panel title="Submit a job">
        {!executionEnabled && (
          <p className="small" style={{ color: "var(--nv)" }}>
            Execution is disabled on this deployment — set
            <code> UVMSTUDIO_API_TOKEN</code> on the API service to enable it.
          </p>
        )}
        <div className="row">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {["compile", "lint", "build", "regress"].map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
          <select
            value={tier}
            onChange={(e) => setTier(e.target.value)}
            disabled={kind !== "regress"}
          >
            {["L0", "L1", "L2", "L3", "L4", "L5"].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <input
            style={{ width: 90 }}
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            placeholder="seed"
          />
          <input
            type="password"
            style={{ width: 190 }}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="API token"
          />
          <button
            className="action"
            onClick={submit}
            disabled={!token || !executionEnabled}
          >
            Run
          </button>
        </div>
        <p className="note">
          The token is held in memory for this tab only — never stored. It
          authorises running a simulator, which executes arbitrary code.
        </p>
      </Panel>

      {active && (
        <Panel title={`Job ${active}`}>
          <pre className="log">{log || "waiting for output…"}</pre>
        </Panel>
      )}

      <Panel title="Recent jobs">
        {jobs.length === 0 ? (
          <Empty>no jobs yet</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Kind</th>
                <th>Project</th>
                <th>State</th>
                <th>Status</th>
                <th className="num">Duration</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr
                  key={j.id}
                  style={{ cursor: "pointer" }}
                  onClick={() => {
                    setActive(j.id);
                    api.jobLog(j.id, 400).then(setLog).catch(setError);
                  }}
                >
                  <td className="mono small">{j.id}</td>
                  <td>{j.kind}</td>
                  <td className="small">{j.project}</td>
                  <td>
                    <Badge status={j.state} />
                  </td>
                  <td>
                    <Badge status={j.status} />
                  </td>
                  <td className="num mono small">
                    {j.duration_s ? `${j.duration_s}s` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}

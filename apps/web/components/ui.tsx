"use client";

import { STATUS_COLOR } from "@/lib/api";

export function Badge({ status }: { status: string }) {
  const color = STATUS_COLOR[status] ?? "var(--muted)";
  return (
    <span className="badge" style={{ color }}>
      {status}
    </span>
  );
}

export function Tile({
  n,
  label,
  color,
}: {
  n: number | string;
  label: string;
  color?: string;
}) {
  return (
    <div className="tile">
      <div className="n" style={color ? { color } : undefined}>
        {n}
      </div>
      <div className="l">{label}</div>
    </div>
  );
}

/**
 * A single-value progress bar.
 *
 * Deliberately not a chart: one percentage against a target is a meter, and a
 * meter reads faster than a bar chart with one bar. Colour encodes the
 * threshold verdict, not the category.
 */
export function Meter({
  percent,
  threshold = 90,
}: {
  percent: number;
  threshold?: number;
}) {
  const pct = Math.max(0, Math.min(100, percent));
  const color = pct >= threshold ? "var(--pass)" : pct >= threshold * 0.7 ? "var(--nv)" : "var(--fail)";
  return (
    <div className="row" style={{ gap: 8 }}>
      <div className="bar" style={{ flex: "0 0 120px" }}>
        <span style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="mono" style={{ color, fontWeight: 600 }}>
        {pct.toFixed(2)}%
      </span>
    </div>
  );
}

export function Panel({
  title,
  children,
  aside,
}: {
  title: string;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <section className="panel">
      <div
        className="row"
        style={{ justifyContent: "space-between", alignItems: "center" }}
      >
        <h2 style={{ margin: 0 }}>{title}</h2>
        {aside}
      </div>
      <div style={{ marginTop: 12 }}>{children}</div>
    </section>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="err">
      <strong style={{ color: "var(--fail)" }}>Error</strong>
      <div className="small mono" style={{ marginTop: 4 }}>
        {msg}
      </div>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="muted small" style={{ margin: "6px 0" }}>
      {children}
    </p>
  );
}

/**
 * Status counts as a compact stacked strip.
 *
 * The whole point of the platform's status discipline is that PASS,
 * NOT_VERIFIED and BLOCKED are different facts. Rendering them as one "not
 * passed" bucket would undo that, so each keeps its own segment and colour.
 */
export function StatusStrip({
  counts,
}: {
  counts: Record<string, number>;
}) {
  const order = ["PASS", "FAIL", "NOT_VERIFIED", "BLOCKED", "ERROR"];
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) return <Empty>no runs</Empty>;
  return (
    <div className="row" style={{ gap: 6 }}>
      <div
        className="bar"
        style={{ display: "flex", flex: "0 0 140px", background: "transparent" }}
      >
        {order
          .filter((k) => counts[k])
          .map((k) => (
            <span
              key={k}
              title={`${k}: ${counts[k]}`}
              style={{
                width: `${(counts[k] / total) * 100}%`,
                background: STATUS_COLOR[k],
              }}
            />
          ))}
      </div>
      <span className="small mono muted">
        {order
          .filter((k) => counts[k])
          .map((k) => `${k.replace("NOT_VERIFIED", "NV")}=${counts[k]}`)
          .join("  ")}
      </span>
    </div>
  );
}

"""Regression reporting — machine-readable first, human-readable second.

`report.json` is the contract CI gates on. The Markdown and HTML views are
rendered from the same data so they can never disagree with it.
"""

from __future__ import annotations

import json
import html
from pathlib import Path
from typing import Any

from .db import RegressionDB

_STATUS_ORDER = ["PASS", "FAIL", "NOT_VERIFIED", "BLOCKED", "ERROR"]
_STATUS_COLOUR = {
    "PASS": "#1a7f37",
    "FAIL": "#cf222e",
    "NOT_VERIFIED": "#9a6700",
    "BLOCKED": "#6e7781",
    "ERROR": "#8250df",
}


def build_report(db: RegressionDB, regression_id: int) -> dict:
    reg = db.regression(regression_id)
    if reg is None:
        raise ValueError(f"no regression with id {regression_id}")
    runs = db.runs(regression_id)

    by_test: dict[str, dict[str, Any]] = {}
    for r in runs:
        e = by_test.setdefault(
            r["test"],
            {"test": r["test"], "tier": r["tier"], "seeds": [],
             "statuses": {}, "failures": []},
        )
        e["seeds"].append(r["seed"])
        e["statuses"][r["status"]] = e["statuses"].get(r["status"], 0) + 1
        if r["status"] in ("FAIL", "NOT_VERIFIED", "BLOCKED", "ERROR"):
            e["failures"].append(
                {
                    "seed": r["seed"],
                    "status": r["status"],
                    "signature": r["failure_signature"],
                    "reasons": json.loads(r["reasons"] or "[]"),
                    "log": r["log_path"],
                    "waves": r["wave_path"],
                    "repro": r["repro_path"],
                }
            )

    clusters = db.clusters(limit=25)

    return {
        "schema": "uvmstudio-report/1",
        "regression": reg,
        "summary": {
            "total": reg["total"], "passed": reg["passed"], "failed": reg["failed"],
            "not_verified": reg["not_verified"], "blocked": reg["blocked"],
            "status": reg["status"],
            "pass_rate": round(reg["passed"] / reg["total"], 4) if reg["total"] else 0.0,
        },
        "tests": sorted(by_test.values(), key=lambda t: t["test"]),
        "runs": runs,
        "failure_clusters": clusters,
    }


def write_json(report: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str))
    return path


def render_markdown(report: dict) -> str:
    s, reg = report["summary"], report["regression"]
    lines = [
        f"# Regression report — {reg['name']}",
        "",
        f"**STATUS: {s['status']}**",
        "",
        "| field | value |",
        "|---|---|",
        f"| project | {reg['project']} |",
        f"| tier | {reg['tier']} |",
        f"| started | {reg['started_utc']} |",
        f"| finished | {reg['finished_utc'] or '-'} |",
        f"| simulator | {reg['backend']} {reg['backend_version'] or ''} |",
        f"| frontend | {reg['frontend_version'] or '-'} |",
        f"| UVM | {reg['uvm_version'] or '-'} |",
        f"| git | {(reg['git_commit'] or '-')[:12]}"
        f"{' (dirty)' if reg['git_dirty'] else ''} on {reg['git_branch'] or '-'} |",
        "",
        "## Totals",
        "",
        "| PASS | FAIL | NOT_VERIFIED | BLOCKED | total |",
        "|---:|---:|---:|---:|---:|",
        f"| {s['passed']} | {s['failed']} | {s['not_verified']} | "
        f"{s['blocked']} | {s['total']} |",
        "",
        "## Per test",
        "",
        "| test | tier | seeds | result |",
        "|---|---|---:|---|",
    ]
    for t in report["tests"]:
        summary = " ".join(f"{k}={v}" for k, v in sorted(t["statuses"].items()))
        lines.append(f"| {t['test']} | {t['tier']} | {len(t['seeds'])} | {summary} |")

    failures = [f for t in report["tests"] for f in t["failures"]]
    if failures:
        lines += ["", "## Failures", ""]
        for t in report["tests"]:
            for f in t["failures"]:
                lines.append(
                    f"- **{t['test']}** seed `{f['seed']}` → `{f['status']}`"
                )
                for r in f["reasons"]:
                    lines.append(f"  - {r}")
                if f["repro"]:
                    lines.append(f"  - reproduce: `uvmstudio reproduce {f['repro']}`")

    if report["failure_clusters"]:
        lines += ["", "## Failure clusters", "",
                  "| occurrences | signature | triage |", "|---:|---|---|"]
        for c in report["failure_clusters"]:
            sig = (c["signature"] or "")[:110].replace("|", "\\|")
            lines.append(f"| {c['occurrences']} | `{sig}` | {c['triage_state']} |")

    lines += ["", "---",
              "_NOT_VERIFIED means no simulator evidence was observed. "
              "It is never counted as PASS._", ""]
    return "\n".join(lines)


def render_html(report: dict) -> str:
    s, reg = report["summary"], report["regression"]

    def badge(status: str) -> str:
        c = _STATUS_COLOUR.get(status, "#6e7781")
        return (
            f'<span style="background:{c};color:#fff;padding:2px 8px;'
            f'border-radius:10px;font-size:12px;font-weight:600">{html.escape(status)}</span>'
        )

    rows = []
    for t in report["tests"]:
        cells = " ".join(
            f"{badge(k)}&nbsp;{v}" for k, v in sorted(
                t["statuses"].items(), key=lambda kv: _STATUS_ORDER.index(kv[0])
                if kv[0] in _STATUS_ORDER else 99
            )
        )
        rows.append(
            f"<tr><td><code>{html.escape(t['test'])}</code></td>"
            f"<td>{html.escape(t['tier'] or '')}</td>"
            f"<td style='text-align:right'>{len(t['seeds'])}</td><td>{cells}</td></tr>"
        )

    fail_rows = []
    for t in report["tests"]:
        for f in t["failures"]:
            reasons = "<br>".join(html.escape(r) for r in f["reasons"])
            fail_rows.append(
                f"<tr><td><code>{html.escape(t['test'])}</code></td>"
                f"<td>{f['seed']}</td><td>{badge(f['status'])}</td>"
                f"<td>{reasons}</td>"
                f"<td><code>{html.escape((f['signature'] or '')[:80])}</code></td></tr>"
            )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Regression — {html.escape(reg['name'])}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font-family: ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif;
        margin: 0; padding: 32px; line-height: 1.5; }}
 h1 {{ font-size: 22px; margin: 0 0 4px; }}
 h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: .06em;
       opacity: .65; margin: 28px 0 8px; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
 th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid #8884; }}
 th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .05em; opacity: .6; }}
 code {{ font-family: ui-monospace,SFMono-Regular,Menlo,monospace; font-size: 12.5px; }}
 .kpis {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 16px 0 4px; }}
 .kpi {{ border: 1px solid #8884; border-radius: 10px; padding: 10px 16px; min-width: 96px; }}
 .kpi .n {{ font-size: 24px; font-weight: 650; font-variant-numeric: tabular-nums; }}
 .kpi .l {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em; opacity: .6; }}
 .meta {{ font-size: 13px; opacity: .75; }}
 .note {{ margin-top: 28px; font-size: 12.5px; opacity: .7; }}
</style></head><body>
<h1>{html.escape(reg['name'])} &nbsp; {badge(s['status'])}</h1>
<div class="meta">
 project <code>{html.escape(reg['project'])}</code> ·
 tier <code>{html.escape(reg['tier'])}</code> ·
 {html.escape(reg['backend'] or '')} {html.escape(reg['backend_version'] or '')} ·
 UVM {html.escape(reg['uvm_version'] or '-')} ·
 git <code>{html.escape((reg['git_commit'] or '-')[:12])}</code>
 {' <b>(dirty)</b>' if reg['git_dirty'] else ''}
</div>
<div class="kpis">
 <div class="kpi"><div class="n">{s['passed']}</div><div class="l">Pass</div></div>
 <div class="kpi"><div class="n">{s['failed']}</div><div class="l">Fail</div></div>
 <div class="kpi"><div class="n">{s['not_verified']}</div><div class="l">Not verified</div></div>
 <div class="kpi"><div class="n">{s['blocked']}</div><div class="l">Blocked</div></div>
 <div class="kpi"><div class="n">{s['total']}</div><div class="l">Total runs</div></div>
</div>
<h2>Per test</h2>
<table><thead><tr><th>Test</th><th>Tier</th><th>Seeds</th><th>Result</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan=4>no runs</td></tr>'}</tbody></table>
{'<h2>Failures</h2><table><thead><tr><th>Test</th><th>Seed</th><th>Status</th>'
 '<th>Reasons</th><th>Signature</th></tr></thead><tbody>'
 + ''.join(fail_rows) + '</tbody></table>' if fail_rows else ''}
<p class="note">NOT_VERIFIED means no simulator evidence was observed for the pass
criteria. It is never counted as PASS.</p>
</body></html>"""


def write_all(db: RegressionDB, regression_id: int, out_dir: Path) -> dict[str, Path]:
    report = build_report(db, regression_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": write_json(report, out_dir / "report.json"),
        "markdown": out_dir / "report.md",
        "html": out_dir / "report.html",
    }
    paths["markdown"].write_text(render_markdown(report), encoding="utf-8")
    paths["html"].write_text(render_html(report), encoding="utf-8")
    return paths

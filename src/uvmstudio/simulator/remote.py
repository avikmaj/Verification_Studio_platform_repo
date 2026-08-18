"""Remote execution backend — `ExecHost.REMOTE`.

Drives a UVM Verification Studio API deployment (Railway or any host) instead
of a local simulator. This is what lets a Windows laptop with no WSL, or a thin
CI runner, execute real simulations.

The shape is deliberately different from a local backend. Build and run are not
separable over HTTP in a useful way — the server owns the workspace and the
artefacts — so this class implements the `ISimulator` contract by submitting a
*job* and polling it, and reports honestly that per-seed local artefacts are not
returned. `RegressionRunner` is not used with this backend; the CLI drives it
through `regress_remote()` instead.

Being explicit about that beats faking a local-looking `build()`/`run()` pair
that silently loses the log, waveform and coverage files.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..core.errors import BackendUnavailable, SimulatorError, UnsupportedFeature
from ..core.logging import get_logger
from ..plugins.interfaces import FeatureStatus
from .base import (
    BuildRequest,
    BuildResult,
    RunRequest,
    RunResult,
    RunStatus,
    Simulator,
)


@dataclass
class RemoteJob:
    id: str
    kind: str
    state: str
    status: str
    result: dict | None = None
    error: str | None = None
    duration_s: float | None = None

    @property
    def finished(self) -> bool:
        return self.state in ("DONE", "FAILED", "CANCELLED")


class RemoteSimulator(Simulator):
    """ISimulator implementation that delegates to a Studio API deployment."""

    name = "remote"

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout_s: float = 30.0,
        **_ignored: Any,
    ) -> None:
        import os

        self.base_url = (
            base_url or os.environ.get("UVMSTUDIO_API_URL", "")
        ).rstrip("/")
        self.token = token or os.environ.get("UVMSTUDIO_API_TOKEN", "")
        self.timeout_s = timeout_s
        self._logger = get_logger()
        self._env_cache: dict | None = None

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None,
                 *, timeout: float | None = None) -> Any:
        if not self.base_url:
            raise BackendUnavailable(
                "remote backend needs UVMSTUDIO_API_URL (or --api-url)"
            )
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout_s) as r:
                raw = r.read().decode("utf-8", errors="replace")
                ctype = r.headers.get("Content-Type", "")
                return json.loads(raw) if "json" in ctype else raw
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise SimulatorError(
                f"remote API {method} {path} -> HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise BackendUnavailable(
                f"cannot reach remote API at {self.base_url}: {exc.reason}"
            ) from exc

    # -- availability ------------------------------------------------------
    def is_available(self) -> bool:
        try:
            return self._request("GET", "/health").get("status") == "ok"
        except Exception:
            return False

    def remote_env(self) -> dict:
        if self._env_cache is None:
            self._env_cache = self._request("GET", "/env")
        return self._env_cache

    def version(self) -> str:
        """Report the *remote* simulator's version — that is what actually ran."""
        try:
            sims = self.remote_env().get("simulators", {})
            for name, info in sims.items():
                if info.get("available"):
                    return f"{name}-{info.get('version')}@remote"
        except Exception:
            pass
        return "unknown@remote"

    def exec_host(self) -> str:
        return f"remote({self.base_url})"

    def capabilities(self) -> dict[str, FeatureStatus]:
        """Mirror the remote deployment's capabilities; never invent them."""
        base = {
            "remote_execution": FeatureStatus.SUPPORTED,
            "job_queue": FeatureStatus.SUPPORTED,
            "streaming_logs": FeatureStatus.SUPPORTED,
            "local_artifact_download": FeatureStatus.PLANNED,
            "separable_build_and_run": FeatureStatus.UNSUPPORTED,
        }
        try:
            for name, info in self.remote_env().get("simulators", {}).items():
                if info.get("available"):
                    for k, v in (info.get("capabilities") or {}).items():
                        base[f"{name}:{k}"] = FeatureStatus(v)
        except Exception:
            base["remote_env_probe"] = FeatureStatus.UNSUPPORTED
        return base

    # -- ISimulator contract ----------------------------------------------
    def build(self, request: BuildRequest) -> BuildResult:
        raise UnsupportedFeature(
            "the remote backend does not expose a separable build step — the "
            "server owns the workspace. Use `regress_remote()` "
            "(`uvmstudio regress --backend remote`), which submits a job that "
            "builds and runs on the deployment."
        )

    def run(self, request: RunRequest) -> RunResult:
        raise UnsupportedFeature(
            "the remote backend does not run a locally-built binary. "
            "Use `regress_remote()`."
        )

    # -- job driving -------------------------------------------------------
    def submit(self, project: str, kind: str, **params: Any) -> RemoteJob:
        payload = {"project": project, "kind": kind, **params}
        return self._to_job(self._request("POST", "/jobs", payload))

    def poll(self, job_id: str) -> RemoteJob:
        return self._to_job(self._request("GET", f"/jobs/{job_id}"))

    def job_log(self, job_id: str, tail: int = 500) -> str:
        out = self._request("GET", f"/jobs/{job_id}/log?tail={tail}")
        return out if isinstance(out, str) else json.dumps(out)

    def wait(
        self,
        job_id: str,
        *,
        poll_s: float = 3.0,
        timeout_s: float = 7200.0,
        on_log: Callable[[str], None] | None = None,
    ) -> RemoteJob:
        """Poll until the job finishes, streaming newly-appended log lines."""
        t0 = time.monotonic()
        seen = 0
        while True:
            job = self.poll(job_id)
            if on_log:
                text = self.job_log(job_id, tail=5000)
                lines = text.splitlines()
                for line in lines[seen:]:
                    on_log(line)
                seen = len(lines)
            if job.finished:
                return job
            if time.monotonic() - t0 > timeout_s:
                raise SimulatorError(
                    f"remote job {job_id} did not finish within {timeout_s}s"
                )
            time.sleep(poll_s)

    def regress_remote(
        self,
        project: str,
        *,
        tier: str = "L1",
        tests: list[str] | None = None,
        seed: int | None = None,
        seeds: int | None = None,
        jobs: int = 2,
        on_log: Callable[[str], None] | None = None,
    ) -> tuple[RunStatus, dict]:
        """Run a regression on the deployment. Returns (status, job payload)."""
        job = self.submit(
            project, "regress", tier=tier, tests=tests, seed=seed,
            seeds=seeds, jobs=jobs,
        )
        self.log_start(job)
        done = self.wait(job.id, on_log=on_log)
        try:
            status = RunStatus(done.status)
        except ValueError:
            status = RunStatus.NOT_VERIFIED
        return status, done.__dict__

    def log_start(self, job: RemoteJob) -> None:
        self._logger.info("remote job submitted", id=job.id, kind=job.kind,
                      host=self.base_url)

    @staticmethod
    def _to_job(d: dict) -> RemoteJob:
        return RemoteJob(
            id=d["id"], kind=d.get("kind", ""), state=d.get("state", ""),
            status=d.get("status", "NOT_VERIFIED"), result=d.get("result"),
            error=d.get("error"), duration_s=d.get("duration_s"),
        )


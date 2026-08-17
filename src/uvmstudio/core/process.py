"""Subprocess execution with timeout, capture, and guaranteed group cleanup.

Simulation binaries fork helper processes and can wedge. Every external tool in
the platform goes through `run()`, which isolates the child in its own process
group (POSIX) or job-control group (Windows) and tears down the whole tree on
timeout — never leaving orphans behind to poison a regression farm.

Cross-platform note: POSIX uses setsid + killpg; Windows has no process groups
in that sense, so it uses CREATE_NEW_PROCESS_GROUP plus `taskkill /T /F`, which
is the only reliable way to kill a process tree there.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from .logging import get_logger

_IS_WINDOWS = sys.platform == "win32"


@dataclass
class ProcResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    cwd: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def combined(self) -> str:
        return self.stdout + ("\n" + self.stderr if self.stderr else "")

    def to_dict(self) -> dict:
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "duration_s": round(self.duration_s, 4),
            "timed_out": self.timed_out,
            "cwd": self.cwd,
        }


@dataclass
class ProcessManager:
    """Runs external tools. One instance per session; safe to share."""

    default_timeout_s: float = 1800.0
    env_overlay: dict[str, str] = field(default_factory=dict)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        log_path: Path | None = None,
        check: bool = False,
    ) -> ProcResult:
        argv = [str(a) for a in argv]
        timeout = self.default_timeout_s if timeout_s is None else timeout_s
        full_env = dict(os.environ)
        full_env.update(self.env_overlay)
        if env:
            full_env.update(env)

        log = get_logger()
        log.debug("exec", argv=" ".join(argv[:8]), cwd=str(cwd or "."))

        t0 = time.monotonic()
        timed_out = False
        # Isolate the child so a timeout can kill its whole tree.
        if _IS_WINDOWS:
            spawn_kwargs = {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            }
        else:
            spawn_kwargs = {"start_new_session": True}

        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd) if cwd else None,
                env=full_env,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                **spawn_kwargs,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"executable not found: {argv[0]}") from exc

        try:
            out, err = proc.communicate(input=stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_group(proc)
            try:
                out, err = proc.communicate(timeout=15)
            except Exception:
                out, err = "", ""
            err = (err or "") + f"\n[uvmstudio] TIMEOUT after {timeout}s — process group killed\n"

        dur = time.monotonic() - t0
        result = ProcResult(
            argv=argv,
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=out or "",
            stderr=err or "",
            duration_s=dur,
            timed_out=timed_out,
            cwd=str(cwd) if cwd else None,
        )

        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"$ {' '.join(argv)}\n"
                f"# cwd={result.cwd} rc={result.returncode} "
                f"dur={result.duration_s:.3f}s timeout={timed_out}\n\n"
                f"{result.stdout}\n{result.stderr}",
                encoding="utf-8",
            )

        if check and not result.ok:
            raise RuntimeError(
                f"command failed (rc={result.returncode}, timeout={timed_out}): "
                f"{' '.join(argv[:6])}\n{result.stderr[-4000:]}"
            )
        return result

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        """Terminate the child and every process it spawned."""
        if _IS_WINDOWS:
            # taskkill /T walks the child tree; nothing in the stdlib does.
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=30,
                )
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        for _ in range(30):
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def which(self, exe: str) -> str | None:
        from shutil import which as _which

        return _which(exe)

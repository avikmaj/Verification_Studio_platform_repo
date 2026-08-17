"""Structured logging for UVM Verification Studio.

Two sinks, always:
  * a human sink on stderr (severity-tagged, optionally coloured)
  * a machine sink as JSON Lines, so regression runs are post-processable

The JSONL sink is what CI and the regression-intelligence layer consume. Never
parse the human sink.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, TextIO


class Severity(IntEnum):
    DEBUG = 10
    INFO = 20
    HINT = 25
    WARNING = 30
    ERROR = 40
    FATAL = 50

    @classmethod
    def parse(cls, name: str) -> "Severity":
        try:
            return cls[name.strip().upper()]
        except KeyError as exc:
            raise ValueError(f"unknown severity {name!r}") from exc


_COLOURS = {
    Severity.DEBUG: "\033[2;37m",
    Severity.INFO: "\033[0;36m",
    Severity.HINT: "\033[0;34m",
    Severity.WARNING: "\033[0;33m",
    Severity.ERROR: "\033[0;31m",
    Severity.FATAL: "\033[1;31m",
}
_RESET = "\033[0m"


def _colour_enabled(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("UVMSTUDIO_FORCE_COLOR") == "1":
        return True
    return hasattr(stream, "isatty") and stream.isatty()


@dataclass
class Logger:
    """Dual-sink logger. Thread-safe; the regression runner logs from workers."""

    name: str = "uvmstudio"
    level: Severity = Severity.INFO
    stream: TextIO = field(default_factory=lambda: sys.stderr)
    jsonl_path: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _jsonl_fh: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.jsonl_path is not None:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            self._jsonl_fh = self.jsonl_path.open("a", encoding="utf-8")

    # -- sinks ------------------------------------------------------------
    def log(self, sev: Severity, msg: str, **fields: Any) -> None:
        record = {
            "ts": time.time(),
            "logger": self.name,
            "severity": sev.name,
            "message": msg,
        }
        if fields:
            record["fields"] = fields

        with self._lock:
            if self._jsonl_fh is not None:
                self._jsonl_fh.write(json.dumps(record, default=str) + "\n")
                self._jsonl_fh.flush()

            if sev >= self.level:
                if _colour_enabled(self.stream):
                    prefix = f"{_COLOURS[sev]}{sev.name:<7}{_RESET}"
                else:
                    prefix = f"{sev.name:<7}"
                extra = ""
                if fields:
                    extra = " " + " ".join(f"{k}={v}" for k, v in fields.items())
                self.stream.write(f"{prefix} {msg}{extra}\n")
                self.stream.flush()

    # -- convenience ------------------------------------------------------
    def debug(self, msg: str, **f: Any) -> None:
        self.log(Severity.DEBUG, msg, **f)

    def info(self, msg: str, **f: Any) -> None:
        self.log(Severity.INFO, msg, **f)

    def hint(self, msg: str, **f: Any) -> None:
        self.log(Severity.HINT, msg, **f)

    def warning(self, msg: str, **f: Any) -> None:
        self.log(Severity.WARNING, msg, **f)

    def error(self, msg: str, **f: Any) -> None:
        self.log(Severity.ERROR, msg, **f)

    def fatal(self, msg: str, **f: Any) -> None:
        self.log(Severity.FATAL, msg, **f)

    def close(self) -> None:
        with self._lock:
            if self._jsonl_fh is not None:
                self._jsonl_fh.close()
                self._jsonl_fh = None


_default: Logger | None = None


def get_logger() -> Logger:
    global _default
    if _default is None:
        lvl = os.environ.get("UVMSTUDIO_LOG_LEVEL", "INFO")
        _default = Logger(level=Severity.parse(lvl))
    return _default


def set_logger(logger: Logger) -> None:
    global _default
    _default = logger

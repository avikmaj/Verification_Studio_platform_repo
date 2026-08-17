"""Backend-neutral diagnostic model.

Every frontend and every simulator normalises its messages into `Diagnostic`.
The IDE, CLI and regression database only ever see this type, so swapping slang
for Surelog — or adding VCS log parsing — does not ripple outward.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import IntEnum
from pathlib import Path


class DiagSeverity(IntEnum):
    IGNORED = 0
    HINT = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    FATAL = 50

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class SourceRef:
    """A clickable location. `file` is absolute; the IDE relativises for display."""

    file: str
    line: int
    column: int
    end_line: int | None = None
    end_column: int | None = None

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"


@dataclass
class Diagnostic:
    severity: DiagSeverity
    message: str
    code: str = ""                       # e.g. "slang:UndeclaredIdentifier"
    location: SourceRef | None = None
    notes: list[str] = field(default_factory=list)
    source_line: str = ""                # the offending line, for terminal output
    producer: str = ""                   # which component emitted it

    @property
    def is_error(self) -> bool:
        return self.severity >= DiagSeverity.ERROR

    def format(self, *, root: Path | None = None) -> str:
        loc = ""
        if self.location:
            f = self.location.file
            if root is not None:
                try:
                    f = str(Path(f).relative_to(root))
                except ValueError:
                    pass
            loc = f"{f}:{self.location.line}:{self.location.column}: "
        code = f" [{self.code}]" if self.code else ""
        head = f"{loc}{self.severity.label}: {self.message}{code}"
        parts = [head]
        if self.source_line:
            parts.append("    " + self.source_line.rstrip())
            if self.location and self.location.column > 0:
                parts.append("    " + " " * (self.location.column - 1) + "^")
        parts.extend(f"    note: {n}" for n in self.notes)
        return "\n".join(parts)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.name
        return d


@dataclass
class DiagnosticBag:
    """Collected diagnostics plus the summary counts CI gates on."""

    items: list[Diagnostic] = field(default_factory=list)

    def add(self, d: Diagnostic) -> None:
        self.items.append(d)

    def extend(self, ds: list[Diagnostic]) -> None:
        self.items.extend(ds)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity >= DiagSeverity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == DiagSeverity.WARNING]

    @property
    def has_errors(self) -> bool:
        return any(d.is_error for d in self.items)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.items:
            out[d.severity.name] = out.get(d.severity.name, 0) + 1
        return out

    def format(self, *, root: Path | None = None, limit: int | None = None) -> str:
        items = self.items if limit is None else self.items[:limit]
        text = "\n".join(d.format(root=root) for d in items)
        if limit is not None and len(self.items) > limit:
            text += f"\n... {len(self.items) - limit} more diagnostic(s) suppressed"
        return text

    def to_dict(self) -> dict:
        return {"counts": self.counts(), "items": [d.to_dict() for d in self.items]}

    def __len__(self) -> int:
        return len(self.items)

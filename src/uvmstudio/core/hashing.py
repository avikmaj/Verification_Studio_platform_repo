"""Deterministic content hashing.

Reproducibility depends on being able to say *exactly* which bytes were
compiled. `source_hash` is a Merkle-style digest over the ordered file list, so
adding, removing, reordering or editing a file all change the hash.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

_CHUNK = 1 << 20


def hash_file(path: Path) -> str:
    """SHA-256 of a single file's bytes."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def hash_sources(paths: Iterable[Path], *, root: Path | None = None) -> str:
    """Order-sensitive digest over a set of source files.

    The relative path is folded into the digest alongside the content so that
    two projects with identical file contents but different layouts hash
    differently — layout affects `include resolution and therefore semantics.
    """
    h = hashlib.sha256()
    h.update(b"uvmstudio-source-set-v1\n")
    for p in paths:
        rel = p.relative_to(root).as_posix() if root else p.as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        h.update(hash_file(p).encode("ascii") + b"\n")
    return h.hexdigest()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short(digest: str, n: int = 12) -> str:
    return digest[:n]

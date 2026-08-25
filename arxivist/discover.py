"""Find candidate PDF files under a source directory."""

from __future__ import annotations

from pathlib import Path
from typing import List

from .config import STATE_DIR


def find_pdfs(source: Path, recursive: bool = True) -> List[Path]:
    """Return PDFs under `source`, skipping arxivist's own state dir and hidden dirs."""
    if source.is_file():
        return [source] if source.suffix.lower() == ".pdf" else []
    globber = source.rglob if recursive else source.glob
    out: List[Path] = []
    for p in globber("*.pdf"):
        if not p.is_file():
            continue
        parts = set(p.relative_to(source).parts[:-1])
        if STATE_DIR in parts:
            continue
        if any(part.startswith(".") for part in parts):
            continue
        out.append(p)
    # Also catch uppercase .PDF on case-sensitive filesystems (Linux dev box).
    for p in (globber("*.PDF") if recursive else source.glob("*.PDF")):
        if p.is_file() and p not in out:
            out.append(p)
    return sorted(out)

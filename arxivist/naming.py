"""Turn a PaperMeta into a safe '<year> - <title>.pdf' filename."""

from __future__ import annotations

import re
from pathlib import Path

from .models import PaperMeta

# Characters illegal on macOS/Windows filesystems, plus control chars.
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_STEM = 180  # keep well under the 255-byte name limit, leaving room for suffixes


def sanitize(name: str) -> str:
    name = _ILLEGAL.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Trailing dots/spaces are invalid on Windows and confusing on macOS.
    name = name.rstrip(" .")
    return name


def build_filename(meta: PaperMeta, fallback_stem: str) -> str:
    """'<year> - <title>.pdf'. Falls back to the original stem when title is unknown."""
    year = str(meta.year) if meta.year else "n.d."
    title = sanitize(meta.title) if meta.title else sanitize(fallback_stem)
    if not title:
        title = "Untitled"
    stem = f"{year} - {title}"
    if len(stem) > _MAX_STEM:
        stem = stem[:_MAX_STEM].rstrip(" .")
    return f"{stem}.pdf"


def unique_path(dest_dir: Path, filename: str) -> Path:
    """A path under dest_dir that doesn't collide, appending ' (2)', ' (3)', ..."""
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    n = 2
    while True:
        candidate = dest_dir / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1

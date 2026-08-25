"""Offline extraction: pull text, embedded metadata, DOI and arXiv id from a PDF.

Everything here works without a network connection. The results feed both the
paper/not-paper detector and the online lookup (which needs an id to query).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .models import PaperMeta

# A DOI: "10." then 4-9 digits, "/", then a run of allowed chars. We deliberately
# stop at whitespace/quotes and trim trailing punctuation that tends to get glued
# on when a DOI sits at the end of a sentence.
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_DOI_TRAILING = ".,;)]}>\"'"

# arXiv identifiers, both the modern (1501.00001) and legacy (math.GT/0309136) forms.
_ARXIV_NEW_RE = re.compile(r"arxiv[:\s]*?(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)
_ARXIV_LEGACY_RE = re.compile(r"arxiv[:\s]*?([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.IGNORECASE)

# A 4-digit year in a plausible publication range.
_YEAR_RE = re.compile(r"\b(19[7-9]\d|20[0-4]\d)\b")


def read_pdf(path: Path, max_pages: int = 3) -> Tuple[str, dict]:
    """Return (first-pages text, embedded metadata dict). Never raises on a bad PDF."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    text_parts: List[str] = []
    info: dict = {}
    try:
        reader = PdfReader(str(path))
    except (PdfReadError, OSError, ValueError, Exception):  # noqa: BLE001 - pypdf raises many types
        return "", {}

    try:
        meta = reader.metadata or {}
        # pypdf exposes DocumentInformation with .title/.author etc.; be defensive.
        info = {
            "title": _clean(getattr(meta, "title", None)),
            "author": _clean(getattr(meta, "author", None)),
            "subject": _clean(getattr(meta, "subject", None)),
            "producer": _clean(getattr(meta, "producer", None)),
            "creation_date": getattr(meta, "creation_date", None),
        }
    except Exception:  # noqa: BLE001
        info = {}

    try:
        for page in reader.pages[:max_pages]:
            try:
                text_parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - a single bad page shouldn't kill the run
                continue
    except Exception:  # noqa: BLE001
        pass

    return "\n".join(text_parts), info


def _clean(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = str(value).strip()
    return value or None


def find_doi(text: str) -> Optional[str]:
    m = _DOI_RE.search(text.replace("\n", " "))
    if not m:
        return None
    return m.group(0).rstrip(_DOI_TRAILING)


def find_arxiv_id(text: str) -> Optional[str]:
    flat = text.replace("\n", " ")
    m = _ARXIV_NEW_RE.search(flat) or _ARXIV_LEGACY_RE.search(flat)
    if not m:
        return None
    return m.group(1)


def guess_title(text: str, info: dict) -> Optional[str]:
    """Best-effort title from embedded metadata, else the first-page layout.

    Heuristic: the title is usually among the first non-trivial lines, is one of
    the longer early lines, and isn't an all-caps header like 'ABSTRACT' or a
    journal banner. This is a fallback for when no DOI/arXiv lookup succeeds.
    """
    embedded = info.get("title")
    if embedded and _looks_like_title(embedded):
        return embedded.strip()

    lines = [ln.strip() for ln in text.splitlines()]
    # Consider the first chunk of the page only.
    candidates = []
    for ln in lines[:40]:
        if len(ln) < 8 or len(ln) > 250:
            continue
        low = ln.lower()
        if low in {"abstract", "introduction"} or low.startswith("abstract"):
            break  # title is above the abstract; stop scanning
        if _is_noise_line(ln):
            continue
        candidates.append(ln)
        if len(candidates) >= 6:
            break
    if not candidates:
        return None
    # Prefer the longest of the first few candidate lines (titles run long).
    candidates.sort(key=len, reverse=True)
    return candidates[0]


def guess_year(text: str, info: dict) -> Optional[int]:
    date = info.get("creation_date")
    if date is not None and getattr(date, "year", None):
        y = int(date.year)
        if 1970 <= y <= 2049:
            return y
    years = [int(y) for y in _YEAR_RE.findall(text)]
    if years:
        # The most frequent plausible year on the first pages is usually the pub year.
        return max(set(years), key=years.count)
    return None


def guess_abstract(text: str) -> Optional[str]:
    low = text.lower()
    idx = low.find("abstract")
    if idx == -1:
        return None
    chunk = text[idx + len("abstract"):].strip(" :\n\t-")
    # Cut at the introduction heading or after a reasonable length.
    stop = re.search(r"\n\s*(1\.?\s+)?introduction\b", chunk, re.IGNORECASE)
    if stop:
        chunk = chunk[: stop.start()]
    chunk = re.sub(r"\s+", " ", chunk).strip()
    return chunk[:1500] or None


def extract_offline(path: Path) -> Tuple[PaperMeta, str]:
    """Populate a PaperMeta from local signals only. Returns (meta, first_pages_text)."""
    text, info = read_pdf(path)
    meta = PaperMeta(
        doi=find_doi(text),
        arxiv_id=find_arxiv_id(text),
        title=guess_title(text, info),
        year=guess_year(text, info),
        abstract=guess_abstract(text),
    )
    if meta.title:
        meta.source = "embedded" if info.get("title") else "heuristic"
    return meta, text


# Embedded /Title values that PDF producers leave as boilerplate — never real titles.
_JUNK_TITLES = {
    "untitled", "title", "document", "unknown", "pdf document", "doc",
    "output", "paper", "manuscript", "new document", "presentation",
}


def _looks_like_title(s: str) -> bool:
    s = s.strip()
    if len(s) < 8 or len(s) > 250:
        return False
    low = s.lower()
    if low in _JUNK_TITLES or low.endswith(".pdf") or "microsoft word" in low:
        return False
    # A single lowercase token (e.g. "untitled", "final", a filename) is not a title.
    if " " not in s and s == low:
        return False
    return True


_NOISE_TOKENS = (
    "http", "www.", "@", "doi", "arxiv", "vol.", "volume", "issn", "isbn",
    "copyright", "©", "preprint", "proceedings of",
)


def _is_noise_line(ln: str) -> bool:
    low = ln.lower()
    if any(tok in low for tok in _NOISE_TOKENS):
        return True
    letters = [c for c in ln if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.9:
        return True  # all-caps banner
    if sum(c.isdigit() for c in ln) > len(ln) * 0.4:
        return True  # mostly digits (page numbers, dates)
    return False

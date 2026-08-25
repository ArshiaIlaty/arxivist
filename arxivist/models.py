"""Shared data structures passed between the pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class PaperMeta:
    """What we know about a PDF's bibliographic identity.

    Fields are filled progressively: first from embedded PDF metadata and the
    first-page text (offline), then enriched/overridden by an online lookup
    (Crossref / arXiv) when a DOI or arXiv id is found.
    """

    title: Optional[str] = None
    year: Optional[int] = None
    authors: List[str] = field(default_factory=list)
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    venue: Optional[str] = None
    abstract: Optional[str] = None
    # Where each piece of info came from, for --verbose / debugging.
    source: str = "none"  # one of: none, embedded, heuristic, crossref, arxiv


@dataclass
class Detection:
    """Result of the 'is this a research paper?' gate."""

    is_paper: bool
    score: float
    reasons: List[str] = field(default_factory=list)


@dataclass
class PlannedMove:
    """A single proposed rename+move, before anything touches the disk."""

    src: Path
    dest: Optional[Path]  # None when we decline to move (not a paper / needs review)
    topic: Optional[str]  # the folder we'd file into (None when routed to _NeedsReview)
    meta: PaperMeta
    detection: Detection
    status: str  # planned | not-paper | needs-review | collision | error
    note: str = ""
    # The classifier's best-guess topic, always set for papers even when routed to
    # _NeedsReview — lets the web UI pre-fill an editable topic for the user.
    suggested_topic: Optional[str] = None

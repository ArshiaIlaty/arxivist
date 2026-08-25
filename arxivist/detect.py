"""The gate: is a given PDF actually a research paper?

We score a handful of cheap signals from the first pages rather than trust any
single one. Invoices, e-books, slides, manuals and forms fail most of these.
"""

from __future__ import annotations

import re
from typing import List

from .models import Detection, PaperMeta

# Section headings and cues that are near-universal in papers but rare elsewhere.
_SECTION_CUES = (
    "abstract", "introduction", "related work", "methodology", "methods",
    "experiments", "evaluation", "results", "discussion", "conclusion",
    "references", "acknowledg", "we propose", "in this paper", "our approach",
)
_NEGATIVE_CUES = (
    "invoice", "purchase order", "receipt", "terms and conditions",
    "table of contents", "chapter 1", "user manual", "installation guide",
    "boarding pass", "slide 1",
)


def detect_paper(text: str, meta: PaperMeta) -> Detection:
    reasons: List[str] = []
    score = 0.0
    low = text.lower()

    if meta.doi:
        score += 0.45
        reasons.append("has DOI")
    if meta.arxiv_id:
        score += 0.45
        reasons.append("has arXiv id")

    if "abstract" in low[:4000]:
        score += 0.2
        reasons.append("abstract near top")

    hits = sum(1 for cue in _SECTION_CUES if cue in low)
    if hits:
        score += min(0.3, 0.06 * hits)
        reasons.append(f"{hits} section cues")

    if re.search(r"\breferences\b|\bbibliography\b", low):
        score += 0.15
        reasons.append("references section")

    # Dense bracketed/parenthetical citations, e.g. [12] or (Smith et al., 2020).
    citations = len(re.findall(r"\[\d{1,3}\]", text)) + len(
        re.findall(r"\b[A-Z][a-z]+ et al\.?,?\s*\(?\d{4}", text)
    )
    if citations >= 5:
        score += 0.2
        reasons.append(f"{citations} citation markers")

    for neg in _NEGATIVE_CUES:
        if neg in low:
            score -= 0.5
            reasons.append(f"negative cue: {neg}")
            break

    # Too little extractable text usually means a scan or a non-article artifact.
    if len(text.strip()) < 400:
        score -= 0.3
        reasons.append("very little text")

    score = max(0.0, min(1.0, score))
    return Detection(is_paper=score >= 0.5, score=score, reasons=reasons)

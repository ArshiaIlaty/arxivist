"""Online enrichment via Crossref (DOI) and the arXiv API.

Neither service requires an API key. Crossref asks that you identify yourself in
the User-Agent ("polite pool"); we do. All calls fail soft: on any network or
parse error we return the input meta unchanged and let the offline heuristics stand.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional

from . import __version__
from .models import PaperMeta

_UA = f"arxivist/{__version__} (https://github.com/; paper organizer; mailto:noreply@example.com)"
_TIMEOUT = 15


def enrich(meta: PaperMeta, enabled: bool = True) -> PaperMeta:
    """Fill title/year/authors/venue from an online source when we have an id."""
    if not enabled:
        return meta
    if meta.doi:
        got = _from_crossref(meta.doi)
        if got:
            return _merge(meta, got)
    if meta.arxiv_id:
        got = _from_arxiv(meta.arxiv_id)
        if got:
            return _merge(meta, got)
    return meta


def _merge(base: PaperMeta, add: PaperMeta) -> PaperMeta:
    """Authoritative online fields win; keep ids and any offline extras."""
    return PaperMeta(
        title=add.title or base.title,
        year=add.year or base.year,
        authors=add.authors or base.authors,
        doi=base.doi or add.doi,
        arxiv_id=base.arxiv_id or add.arxiv_id,
        venue=add.venue or base.venue,
        abstract=base.abstract or add.abstract,
        source=add.source,
    )


def _get(url: str, headers: Optional[dict] = None):
    import requests

    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    return requests.get(url, headers=h, timeout=_TIMEOUT)


def _from_crossref(doi: str) -> Optional[PaperMeta]:
    try:
        resp = _get(f"https://api.crossref.org/works/{doi}", {"Accept": "application/json"})
        if resp.status_code != 200:
            return None
        msg = resp.json().get("message", {})
    except Exception:  # noqa: BLE001
        return None

    titles = msg.get("title") or []
    title = titles[0].strip() if titles else None
    year = _crossref_year(msg)
    authors = []
    for a in msg.get("author", []) or []:
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p).strip()
        if name:
            authors.append(name)
    containers = msg.get("container-title") or []
    venue = containers[0] if containers else None
    if not title:
        return None
    return PaperMeta(title=title, year=year, authors=authors, doi=doi, venue=venue, source="crossref")


def _crossref_year(msg: dict) -> Optional[int]:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        parts = ((msg.get(key) or {}).get("date-parts") or [[None]])
        if parts and parts[0] and parts[0][0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _from_arxiv(arxiv_id: str) -> Optional[PaperMeta]:
    try:
        resp = _get(f"http://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1")
        if resp.status_code != 200:
            return None
        root = ET.fromstring(resp.content)
    except Exception:  # noqa: BLE001
        return None

    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        return None

    def txt(tag: str) -> Optional[str]:
        el = entry.find(f"a:{tag}", ns)
        return " ".join(el.text.split()) if (el is not None and el.text) else None

    title = txt("title")
    if not title:
        return None
    published = txt("published") or ""
    year = None
    if len(published) >= 4 and published[:4].isdigit():
        year = int(published[:4])
    authors: List[str] = []
    for a in entry.findall("a:author", ns):
        name_el = a.find("a:name", ns)
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())
    abstract = txt("summary")
    return PaperMeta(
        title=title, year=year, authors=authors, arxiv_id=arxiv_id,
        venue="arXiv", abstract=abstract, source="arxiv",
    )

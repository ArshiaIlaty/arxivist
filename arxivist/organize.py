"""Plan and apply the rename+move, and record a manifest so runs can be undone."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

from .classify import Classifier
from .config import NEEDS_REVIEW, Config, existing_topics
from .detect import detect_paper
from .extract import extract_offline
from .lookup import enrich
from .models import PaperMeta, PlannedMove
from .naming import build_filename, unique_path


def iter_plans(pdfs: Iterable[Path], cfg: Config) -> Iterator[PlannedMove]:
    """Yield one PlannedMove per PDF as it is analyzed (read-only).

    Topics discovered during this run are added to the working set so a second
    paper on the same new subject files next to the first instead of spawning a
    duplicate folder. Used both by the CLI and by the web UI's live stream.
    """
    topics = list(existing_topics(cfg.dest_root))
    classifier = Classifier(cfg)
    for pdf in pdfs:
        yield _plan_one(pdf, cfg, topics, classifier)


def plan_moves(pdfs: Iterable[Path], cfg: Config, progress=None) -> List[PlannedMove]:
    """Collect all planned moves. `progress` is called with each PDF before analysis."""
    pdfs = list(pdfs)
    topics = list(existing_topics(cfg.dest_root))
    classifier = Classifier(cfg)
    plans: List[PlannedMove] = []
    for pdf in pdfs:
        if progress is not None:
            progress(pdf)
        plans.append(_plan_one(pdf, cfg, topics, classifier))
    return plans


def _plan_one(pdf: Path, cfg: Config, topics: List[str], classifier: Classifier) -> PlannedMove:
    try:
        meta, text = extract_offline(pdf)
    except Exception as exc:  # noqa: BLE001
        return PlannedMove(pdf, None, None, PaperMeta(), _empty_detection(),
                           status="error", note=f"extract failed: {exc}")

    detection = detect_paper(text, meta)
    if detection.score < cfg.paper_threshold:
        return PlannedMove(pdf, None, None, meta, detection,
                           status="not-paper", note="; ".join(detection.reasons))

    meta = enrich(meta, enabled=cfg.use_online)

    topic, is_new, confidence = classifier.classify(meta, topics)
    if confidence < cfg.topic_confidence_floor:
        dest_dir = cfg.dest_root / NEEDS_REVIEW
        chosen_topic = None
        note = f"low topic confidence ({confidence:.2f}); best guess: {topic}"
    else:
        dest_dir = cfg.dest_root / topic
        chosen_topic = topic
        note = f"topic '{topic}'{' (new)' if is_new else ''} conf={confidence:.2f}"
        if is_new and topic not in topics:
            topics.append(topic)

    filename = build_filename(meta, fallback_stem=pdf.stem)
    dest = unique_path(dest_dir, filename)
    status = "planned"
    if dest != dest_dir / filename:
        status = "collision"
        note += "; name existed, will suffix"
    return PlannedMove(pdf, dest, chosen_topic, meta, detection, status=status, note=note)


def _empty_detection():
    from .models import Detection

    return Detection(is_paper=False, score=0.0, reasons=[])


def apply_moves(plans: List[PlannedMove], cfg: Config, run_id: str) -> Path:
    """Execute the movable plans, writing a JSONL manifest for `arxivist undo`."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    manifest = cfg.state_dir / f"manifest-{run_id}.jsonl"
    with open(manifest, "w", encoding="utf-8") as log:
        for plan in plans:
            if plan.dest is None or plan.status in {"not-paper", "error"}:
                continue
            plan.dest.parent.mkdir(parents=True, exist_ok=True)
            # unique_path was computed at plan time; re-check to stay safe if the
            # tree changed between plan and apply.
            final = plan.dest if not plan.dest.exists() else unique_path(plan.dest.parent, plan.dest.name)
            shutil.move(str(plan.src), str(final))
            log.write(json.dumps({"src": str(plan.src), "dest": str(final)}) + "\n")
    return manifest


def latest_manifest(cfg: Config) -> Optional[Path]:
    if not cfg.state_dir.exists():
        return None
    manifests = sorted(cfg.state_dir.glob("manifest-*.jsonl"))
    return manifests[-1] if manifests else None


def undo(manifest: Path) -> List[str]:
    """Move files back to their original locations. Returns human-readable notes."""
    notes: List[str] = []
    entries = []
    with open(manifest, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    # Reverse order so any suffixed collisions unwind cleanly.
    for entry in reversed(entries):
        src, dest = Path(entry["src"]), Path(entry["dest"])
        if not dest.exists():
            notes.append(f"skip (missing): {dest}")
            continue
        if src.exists():
            notes.append(f"skip (original path taken): {src}")
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(src))
        notes.append(f"restored: {src.name}")
    return notes

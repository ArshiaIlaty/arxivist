"""FastAPI server: upload PDFs, watch analysis stream in, download the organized set.

Designed for the "app on a server, documents on my Mac" case: you upload PDFs
from the browser, arxivist analyzes + files them into topic folders inside a
per-session workspace on the server, streams progress back over SSE, and hands
you a zip mirroring the intended library layout to drop into your real library.
"""

import io
import json
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from ..classify import Classifier
from ..config import NEEDS_REVIEW, STATE_DIR, load_config
from ..discover import find_pdfs
from ..models import PlannedMove
from ..naming import sanitize
from ..organize import dest_for_topic, iter_plans

_SAFE = re.compile(r"[^A-Za-z0-9._ -]")


@dataclass
class Session:
    id: str
    root: Path
    filenames: List[str] = field(default_factory=list)
    plans: List[PlannedMove] = field(default_factory=list)
    done: bool = False

    @property
    def incoming(self) -> Path:
        return self.root / "incoming"

    @property
    def organized(self) -> Path:
        return self.root / "organized"


def _safe_name(name: str) -> str:
    name = Path(name).name  # strip any path components
    name = _SAFE.sub("_", name).strip() or "upload.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def _plan_to_dict(plan: PlannedMove, dest_root: Path) -> dict:
    dest_rel = None
    if plan.dest is not None:
        try:
            dest_rel = str(plan.dest.relative_to(dest_root))
        except ValueError:
            dest_rel = plan.dest.name
    return {
        "type": "file",
        "source": plan.src.name,
        "status": plan.status,
        "is_paper": plan.detection.is_paper,
        "score": round(plan.detection.score, 2),
        "title": plan.meta.title,
        "year": plan.meta.year,
        "topic": plan.topic,
        "suggested_topic": plan.suggested_topic,
        "meta_source": plan.meta.source,
        "dest": dest_rel,
        "note": plan.note,
    }


def create_app(config_path: Optional[Path] = None, workdir: Optional[Path] = None):
    base = Path(workdir) if workdir else Path(tempfile.gettempdir()) / "arxivist-web"
    base.mkdir(parents=True, exist_ok=True)
    static_dir = Path(__file__).parent / "static"

    app = FastAPI(title="arxivist", docs_url=None, redoc_url=None)
    sessions: Dict[str, Session] = {}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/favicon.ico")
    def favicon():
        # Browsers auto-request this; we don't ship an icon, so answer cleanly
        # instead of logging a 404 on every page load.
        return Response(status_code=204)

    @app.get("/health")
    def health():
        cfg = load_config(base, config_path)
        return {
            "status": "ok",
            "provider": cfg.provider,
            "model": cfg.bedrock_model if cfg.provider == "bedrock" else cfg.model,
            "use_llm": cfg.use_llm,
            "use_online": cfg.use_online,
        }

    @app.post("/api/sessions")
    async def create_session(files: List[UploadFile] = File(...)):
        sid = uuid.uuid4().hex[:12]
        sess = Session(id=sid, root=base / sid)
        sess.incoming.mkdir(parents=True, exist_ok=True)
        saved = 0
        for f in files:
            name = _safe_name(f.filename or "upload.pdf")
            target = sess.incoming / name
            # De-dupe names within a batch.
            n = 2
            while target.exists():
                target = sess.incoming / f"{Path(name).stem} ({n}){Path(name).suffix}"
                n += 1
            data = await f.read()
            target.write_bytes(data)
            sess.filenames.append(target.name)
            saved += 1
        if saved == 0:
            raise HTTPException(status_code=400, detail="No files uploaded.")
        sessions[sid] = sess
        return {"session": sid, "count": saved}

    @app.get("/api/sessions/{sid}/events")
    def stream(sid: str):
        sess = sessions.get(sid)
        if sess is None:
            raise HTTPException(status_code=404, detail="Unknown session.")

        cfg = load_config(sess.organized, config_path)

        def gen():
            pdfs = find_pdfs(sess.incoming, recursive=False)
            total = len(pdfs)
            yield _sse({"type": "start", "total": total,
                        "provider": cfg.provider, "use_llm": cfg.use_llm,
                        "use_online": cfg.use_online})
            plans: List[PlannedMove] = []
            classifier = Classifier(cfg)
            try:
                # Analysis only — no files are moved here. The user reviews/edits
                # topics, then POSTs /apply to file them.
                for i, plan in enumerate(iter_plans(pdfs, cfg, classifier), start=1):
                    plans.append(plan)
                    payload = _plan_to_dict(plan, sess.organized)
                    payload["index"] = i
                    yield _sse(payload)
            except Exception as exc:  # noqa: BLE001
                yield _sse({"type": "error", "message": str(exc)})
                return

            sess.plans = plans
            candidates = sum(1 for p in plans if p.status != "not-paper" and p.status != "error")
            not_paper = sum(1 for p in plans if p.status == "not-paper")
            known = sorted(set(cfg.topics) | {p.topic for p in plans if p.topic}
                           | {p.suggested_topic for p in plans if p.suggested_topic})
            yield _sse({"type": "done", "candidates": candidates, "not_paper": not_paper,
                        "total": total, "known_topics": known,
                        "classifier": classifier.status()})

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/sessions/{sid}/apply")
    def apply(sid: str, payload: dict = Body(default={})):
        """File papers into topic folders, honoring per-file topic overrides.

        Copies (not moves) from the upload area so the user can re-apply with
        different topics. `overrides` maps source filename -> topic; an empty or
        missing topic means "don't file this one".
        """
        sess = sessions.get(sid)
        if sess is None:
            raise HTTPException(status_code=404, detail="Unknown session.")
        if not sess.plans:
            raise HTTPException(status_code=409, detail="Analyze the files first.")

        overrides = payload.get("overrides", {}) or {}
        cfg = load_config(sess.organized, config_path)

        # Rebuild the organized tree from scratch so re-apply is idempotent.
        if sess.organized.exists():
            shutil.rmtree(sess.organized)
        sess.organized.mkdir(parents=True, exist_ok=True)

        filed = 0
        for plan in sess.plans:
            if plan.status == "error" or not plan.src.exists():
                continue
            name = plan.src.name
            if name in overrides:
                topic = sanitize(str(overrides[name] or "")).strip()
            else:  # untouched row: fall back to the planned decision
                topic = plan.topic or (NEEDS_REVIEW if plan.suggested_topic else "")
            if not topic:
                continue  # user chose not to file this one
            dest = dest_for_topic(cfg, plan.meta, plan.src.stem, topic)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(plan.src), str(dest))
            filed += 1

        sess.done = True
        return {"filed": filed, "download": f"api/sessions/{sid}/download"}

    @app.get("/api/sessions/{sid}/download")
    def download(sid: str):
        sess = sessions.get(sid)
        if sess is None or not sess.organized.exists():
            raise HTTPException(status_code=404, detail="Nothing to download.")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(sess.organized.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(sess.organized)
                if rel.parts and rel.parts[0] == STATE_DIR:
                    continue  # don't ship the internal manifest dir
                zf.write(path, arcname=str(Path("organized") / rel))
        buf.seek(0)
        headers = {"Content-Disposition": 'attachment; filename="arxivist-organized.zip"'}
        return Response(content=buf.getvalue(), media_type="application/zip", headers=headers)

    return app


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"

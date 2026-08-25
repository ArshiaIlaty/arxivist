"""Adaptive topic classification for a narrow, highly-similar corpus.

The corpus here is a single close domain (e.g. synthetic data generation,
survival synthetic data generation, survival analysis, ...), so coarse field
labels are useless. Instead we grow a *fine-grained* taxonomy that matches the
library as it fills up:

  1. Gather the topic folders that already exist under the destination root,
     plus any topics the user curated in config.
  2. Ask the model to either REUSE one of those topics or, only when nothing
     fits, propose a new concise one — strongly preferring reuse so the tree
     stays tidy and folders don't fragment into near-duplicates.
  3. If the LLM is unavailable or unconfident, fall back to keyword matching,
     then to _Unsorted.

Works against either the Anthropic API or Amazon Bedrock (see llm.py). The user
can rename/merge folders on disk at any time; the next run reads the new set and
adapts.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .config import UNSORTED, Config
from .llm import build_client, force_tool, resolve_model
from .models import PaperMeta

# Tool input schema: the model must return exactly these fields.
_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {
            "type": "string",
            "description": "Concise Title Case topic folder name, e.g. 'Survival Analysis'.",
        },
        "is_new": {
            "type": "boolean",
            "description": "True only if this topic is NOT among the existing topics provided.",
        },
        "confidence": {
            "type": "number",
            "description": "0..1 confidence that this is the right topic for the paper.",
        },
        "reason": {"type": "string", "description": "One short sentence."},
    },
    "required": ["topic", "is_new", "confidence", "reason"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You file academic papers into a fine-grained topic taxonomy for a single "
    "researcher whose whole library sits in one closely related domain. Because "
    "the papers are all similar, distinctions must be specific (e.g. 'Survival "
    "Synthetic Data Generation' vs 'Survival Analysis' vs 'Synthetic Data "
    "Generation'), never broad field labels. STRONGLY prefer reusing an existing "
    "topic over inventing one; only create a new topic when the paper genuinely "
    "does not belong to any existing one. New topics must be concise, Title Case, "
    "and phrased like the existing ones."
)

_TOOL_DESC = "Record the single best topic folder for this paper."


class Classifier:
    """Holds a reusable LLM client across a batch; falls back to keywords."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = None
        self._client_tried = False

    def _get_client(self):
        if not self._client_tried:
            self._client_tried = True
            try:
                self._client = build_client(self.cfg)
            except Exception:  # noqa: BLE001 - SDK missing or no creds
                self._client = None
        return self._client

    def classify(self, meta: PaperMeta, existing: List[str]) -> Tuple[str, bool, float]:
        """Return (topic, is_new, confidence)."""
        if self.cfg.use_llm:
            got = self._classify_llm(meta, existing)
            if got is not None:
                return got
        kw = self._classify_keywords(meta)
        if kw:
            return kw, kw not in existing, 0.4
        return UNSORTED, False, 0.0

    def _classify_llm(self, meta: PaperMeta, existing: List[str]) -> Optional[Tuple[str, bool, float]]:
        text = _text_for(meta)
        if not text:
            return None
        client = self._get_client()
        if client is None:
            return None

        existing_block = "\n".join(f"- {t}" for t in existing) if existing else "(none yet)"
        curated = [t for t in self.cfg.topics if t not in existing]
        curated_block = (
            "\nUser-suggested topics you may also use:\n" + "\n".join(f"- {t}" for t in curated)
            if curated else ""
        )
        prompt = (
            f"Existing topic folders:\n{existing_block}{curated_block}\n\n"
            f"Paper to file:\n{text[:4000]}\n\n"
            "Pick the single best topic. Reuse an existing topic unless none fits."
        )

        data = force_tool(
            client, resolve_model(self.cfg), _SYSTEM, prompt,
            tool_name="select_topic", tool_description=_TOOL_DESC, schema=_SCHEMA,
        )
        if not data:
            return None
        topic = str(data.get("topic", "")).strip()
        if not topic:
            return None
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        # Trust the on-disk/working set over the model's self-report for is_new.
        return topic, topic not in existing, confidence

    def _classify_keywords(self, meta: PaperMeta) -> Optional[str]:
        text = _text_for(meta).lower()
        if not text or not self.cfg.topics:
            return None
        best, best_hits = None, 0
        for topic, keywords in self.cfg.topics.items():
            hits = sum(1 for kw in keywords if kw and kw in text)
            if hits > best_hits:
                best, best_hits = topic, hits
        return best if best_hits > 0 else None


def _text_for(meta: PaperMeta) -> str:
    bits = [meta.title or "", meta.abstract or "", meta.venue or ""]
    return "\n".join(b for b in bits if b).strip()

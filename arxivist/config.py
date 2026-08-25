"""Configuration: defaults, an optional YAML file, and the topic list."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Special folders under the destination root that are never treated as topics.
UNSORTED = "_Unsorted"
NEEDS_REVIEW = "_NeedsReview"
STATE_DIR = ".arxivist"
RESERVED = {UNSORTED, NEEDS_REVIEW, STATE_DIR}


@dataclass
class Config:
    dest_root: Path
    # LLM provider: "anthropic" (first-party API key) or "bedrock" (Amazon Bedrock).
    provider: str = "anthropic"
    model: str = "claude-opus-5"
    # Bedrock uses its own model ids / inference profiles; falls back to `model`.
    bedrock_model: Optional[str] = None
    aws_region: Optional[str] = None
    use_llm: bool = True
    use_online: bool = True
    paper_threshold: float = 0.5
    # Below this classifier confidence, a paper goes to _NeedsReview instead of a topic.
    topic_confidence_floor: float = 0.35
    # User-curated topics: {topic_name: [keyword, ...]}. Keywords power the
    # offline fallback classifier and hint the LLM.
    topics: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        return self.dest_root / STATE_DIR


def load_config(dest_root: Path, config_path: Optional[Path]) -> Config:
    data: dict = {}
    path = config_path or _default_config_path()
    if path and path.exists():
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    topics_raw = data.get("topics", {}) or {}
    topics: Dict[str, List[str]] = {}
    if isinstance(topics_raw, dict):
        for name, kws in topics_raw.items():
            topics[str(name)] = [str(k).lower() for k in (kws or [])]
    elif isinstance(topics_raw, list):  # allow a bare list of topic names
        for name in topics_raw:
            topics[str(name)] = []

    cfg = Config(dest_root=dest_root, topics=topics)
    if "provider" in data:
        cfg.provider = str(data["provider"]).lower()
    if "model" in data:
        cfg.model = str(data["model"])
    if "bedrock_model" in data:
        cfg.bedrock_model = str(data["bedrock_model"])
    if "aws_region" in data:
        cfg.aws_region = str(data["aws_region"])
    if "use_llm" in data:
        cfg.use_llm = bool(data["use_llm"])
    if "use_online" in data:
        cfg.use_online = bool(data["use_online"])
    if "paper_threshold" in data:
        cfg.paper_threshold = float(data["paper_threshold"])
    if "topic_confidence_floor" in data:
        cfg.topic_confidence_floor = float(data["topic_confidence_floor"])

    # Environment overrides win over the file (handy for servers/containers).
    cfg.provider = os.environ.get("ARXIVIST_PROVIDER", cfg.provider).lower()
    cfg.model = os.environ.get("ARXIVIST_MODEL", cfg.model)
    cfg.bedrock_model = os.environ.get("ARXIVIST_BEDROCK_MODEL", cfg.bedrock_model)
    cfg.aws_region = os.environ.get("AWS_REGION", cfg.aws_region or "") or None
    return cfg


def _default_config_path() -> Optional[Path]:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "arxivist" / "config.yaml"


def existing_topics(dest_root: Path) -> List[str]:
    """Topic folders already present under the destination root (sorted)."""
    if not dest_root.exists():
        return []
    names = [
        p.name
        for p in dest_root.iterdir()
        if p.is_dir() and p.name not in RESERVED and not p.name.startswith(".")
    ]
    return sorted(names)

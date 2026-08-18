from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Topic:
    title: str
    context: str
    visual_hint: str
    fingerprint: str
    source: str
    format_hint: str = "explainer"
    kind: str = "evergreen"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scene:
    voiceover: str
    visual_query: str
    overlay_text: str


@dataclass
class Script:
    topic: str
    hook: str
    scenes: list[Scene]
    title: str
    description: str
    tags: list[str]
    source_note: str
    quality_score: float = 0.0

    @property
    def narration(self) -> str:
        return " ".join(s.voiceover.strip() for s in self.scenes if s.voiceover.strip()).strip()

    @property
    def visual_queries(self) -> list[str]:
        return [s.visual_query for s in self.scenes if s.visual_query]


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


@dataclass
class SpeechResult:
    path: Path
    provider: str
    duration: float
    words: list[WordTiming] = field(default_factory=list)


@dataclass
class MediaClip:
    video_id: int | None
    query: str
    url: str = ""
    duration: float = 8.0
    width: int = 1080
    height: int = 1920
    local_path: Path | None = None
    creator_name: str = ""
    creator_url: str = ""
    pexels_url: str = ""
    source: str = "generated"
    is_image: bool = False

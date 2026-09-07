"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    anthropic_api_key: str = ""
    voyage_api_key: str = ""

    # Keep the most capable model here. A segmentation or tagging error is
    # written to the database once and then silently degrades every feature
    # built on top of it, so this is the wrong place to economise — the saving
    # is a few hundred dollars, the cost is a question bank nobody trusts.
    extraction_model: str = "claude-opus-5"
    embedding_model: str = "voyage-3"

    # 200 DPI is the floor at which CAIE's smaller subscripts and circuit
    # symbols survive rasterisation; below it the model starts guessing at
    # things like v₁ vs v₂.
    render_dpi: int = 200

    # Tagging below this confidence goes to a human instead of to students.
    tag_confidence_floor: float = 0.75

    work_dir: Path = field(default_factory=lambda: Path("pipeline/work"))

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            voyage_api_key=os.environ.get("VOYAGE_API_KEY", ""),
            extraction_model=os.environ.get(
                "NOTEACADEMY_EXTRACTION_MODEL", cls.extraction_model
            ),
            embedding_model=os.environ.get(
                "NOTEACADEMY_EMBEDDING_MODEL", cls.embedding_model
            ),
            render_dpi=int(os.environ.get("NOTEACADEMY_RENDER_DPI", cls.render_dpi)),
            work_dir=Path(os.environ.get("NOTEACADEMY_WORK_DIR", "pipeline/work")),
        )


settings = Settings.from_env()

"""Runtime configuration, read once from the environment.

A `.env` at the repository root is loaded first, so the CLI works from a
checkout without exporting anything by hand. Real environment variables always
win over the file — that is what lets CI and production override it without
editing anything on disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from the repository root's .env, if present.

    Deliberately hand-rolled and tiny: the pipeline should not grow a dependency
    to read eight lines of config, and this avoids any chance of a library
    silently overriding a variable the environment already set.
    """
    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = directory / ".env"
        if not candidate.is_file():
            continue

        for raw in candidate.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            # Never clobber a variable the environment already provides.
            if key and key not in os.environ:
                os.environ[key] = value
        return


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    anthropic_api_key: str = ""
    voyage_api_key: str = ""

    # Supabase Storage. The service role key is the only credential needed, and
    # it is the same one the web app uses server-side — never shipped to a
    # browser, and never prefixed NEXT_PUBLIC_.
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    storage_bucket: str = ""

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
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            storage_bucket=os.environ.get("STORAGE_BUCKET", ""),
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

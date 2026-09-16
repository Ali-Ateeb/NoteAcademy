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
    google_api_key: str = ""
    voyage_api_key: str = ""

    # A second provider for the two stages that call a model directly
    # (extract.py's vision pass, tagging.py's direct-API tagger), for exactly
    # the situation that motivated adding it: Gemini returning a persistent
    # 503 with real Qwen/ModelScope credits sitting unused. "gemini" is the
    # default and always what runs unless a stage's own provider setting is
    # switched to "modelscope" — nothing changes for a setup that never
    # touches this.
    modelscope_api_key: str = ""
    tagging_provider: str = "gemini"
    extraction_provider: str = "gemini"
    # Separate settings for text vs. vision in principle — extract.py always
    # needs a vision-capable model, tagging.py never does — but both default
    # to the same model here because the only models this account currently
    # has access to (a "Qwen-Ambassador" pre-release program, confirmed
    # against ModelScope's own sample code) are all Image-Text-to-Text: a VLM
    # answers a text-only prompt fine, it just doesn't need to.
    modelscope_text_model: str = "Qwen-Ambassador/Qwen3.8-Max"
    modelscope_vision_model: str = "Qwen-Ambassador/Qwen3.8-Max"

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
    #
    # That principle assumes a paid setup; it does not have one yet. The Pro
    # tier (gemini-3.1-pro-preview) returns a 429 with a hard 0 free-tier quota
    # on this key — Pro is not available on Google AI Studio's free tier at
    # all, not merely rate-limited — so this is Flash until billing is turned
    # on. Verified against a real paper: 40/40 questions matched, cross-check
    # flags on 8/20 pages that turned out to be the regex heuristic's own false
    # positives (a real question number only appearing embedded in a longer
    # figure like "9.0 N"), not missed or hallucinated questions.
    extraction_model: str = "gemini-3.6-flash"
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
            google_api_key=os.environ.get("GOOGLE_API_KEY", ""),
            voyage_api_key=os.environ.get("VOYAGE_API_KEY", ""),
            modelscope_api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
            tagging_provider=os.environ.get(
                "NOTEACADEMY_TAGGING_PROVIDER", cls.tagging_provider
            ),
            extraction_provider=os.environ.get(
                "NOTEACADEMY_EXTRACTION_PROVIDER", cls.extraction_provider
            ),
            modelscope_text_model=os.environ.get(
                "NOTEACADEMY_MODELSCOPE_TEXT_MODEL", cls.modelscope_text_model
            ),
            modelscope_vision_model=os.environ.get(
                "NOTEACADEMY_MODELSCOPE_VISION_MODEL", cls.modelscope_vision_model
            ),
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

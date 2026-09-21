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


_PROVIDERS = ("gemini", "deepseek")


def _provider(name: str, default: str) -> str:
    """A stage's provider, validated. An unrecognised value must fail here: the
    dispatch in extract.py and tagging.py falls through to Gemini for anything
    that is not "deepseek", so a stale or mistyped value (say, a leftover
    "modelscope" after that provider was removed) would otherwise quietly run
    the paid Gemini path the setting was meant to avoid."""
    value = os.environ.get(name, default).strip().lower()
    if value not in _PROVIDERS:
        raise ValueError(
            f"{name}={value!r} is not a known provider; expected one of {', '.join(_PROVIDERS)}"
        )
    return value


def _year(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        year = int(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a year") from None
    if not 1990 <= year <= 2100:
        raise ValueError(f"{name}={year} is not a plausible exam year")
    return year


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    google_api_key: str = ""
    voyage_api_key: str = ""

    # A second provider for the two stages that call a model directly
    # (extract.py's vision pass, tagging.py's direct-API tagger). "gemini" is
    # the default and always what runs unless a stage's own provider setting
    # is switched to "deepseek" — nothing changes for a setup that never
    # touches this.
    deepseek_api_key: str = ""
    tagging_provider: str = "gemini"
    extraction_provider: str = "gemini"
    # Text and vision are separate settings because DeepSeek's models differ:
    # `deepseek-v4-pro` is the stronger one and is text-only, while
    # `deepseek-flash` is the one that reads images. Tagging never needs
    # vision, so it gets the stronger model; anything that shows a model a
    # page or a crop (extraction, tag_from_crop) must use the vision model.
    # Model names are DeepSeek's own as of the docs read on 2026-09-21 —
    # override them if the lineup has moved.
    deepseek_text_model: str = "deepseek-v4-pro"
    deepseek_vision_model: str = "deepseek-flash"
    # DeepSeek's thinking mode is on by default at the API and is billed as
    # output tokens; off, a classification call answers in seconds. Off here
    # because these stages run sequentially over thousands of items, where
    # latency is the difference between an evening and a weekend — turn it on
    # for a stage where accuracy is worth the wait.
    deepseek_thinking: bool = False

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

    # The sittings the pipeline is working through. Papers outside this range
    # stay in the database and stay live; the commands that spend model calls
    # on them (the automated tag verifiers) simply skip them by default, and
    # take --from-year/--to-year to override for one run.
    scope_year_from: int = 2016
    scope_year_to: int = 2026

    work_dir: Path = field(default_factory=lambda: Path("pipeline/work"))

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            google_api_key=os.environ.get("GOOGLE_API_KEY", ""),
            voyage_api_key=os.environ.get("VOYAGE_API_KEY", ""),
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            tagging_provider=_provider(
                "NOTEACADEMY_TAGGING_PROVIDER", cls.tagging_provider
            ),
            extraction_provider=_provider(
                "NOTEACADEMY_EXTRACTION_PROVIDER", cls.extraction_provider
            ),
            deepseek_text_model=os.environ.get(
                "NOTEACADEMY_DEEPSEEK_TEXT_MODEL", cls.deepseek_text_model
            ),
            deepseek_vision_model=os.environ.get(
                "NOTEACADEMY_DEEPSEEK_VISION_MODEL", cls.deepseek_vision_model
            ),
            deepseek_thinking=os.environ.get("NOTEACADEMY_DEEPSEEK_THINKING", "").strip().lower()
            in ("1", "true", "yes", "on"),
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
            scope_year_from=_year("NOTEACADEMY_YEAR_FROM", cls.scope_year_from),
            scope_year_to=_year("NOTEACADEMY_YEAR_TO", cls.scope_year_to),
            work_dir=Path(os.environ.get("NOTEACADEMY_WORK_DIR", "pipeline/work")),
        )

    def __post_init__(self) -> None:
        if self.scope_year_from > self.scope_year_to:
            raise ValueError(
                f"year scope is backwards: from {self.scope_year_from} to {self.scope_year_to}"
            )


settings = Settings.from_env()

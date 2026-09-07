"""Turn a PDF into page images plus the embedded text layer.

CAIE PDFs are digitally produced and almost always carry a real text layer. That
layer is ground truth: it is used to sanity-check what the vision model returns,
which is how hallucinated question numbers get caught before they reach the
database. Scanned papers (rare, mostly pre-2005) have no text layer and fall back
to vision alone with a flag set.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .config import settings


@dataclass
class RenderedPage:
    page_number: int          # 1-indexed, as printed
    png_path: Path
    text: str                 # embedded text layer, empty if scanned
    width_pt: float
    height_pt: float

    @property
    def has_text_layer(self) -> bool:
        return len(self.text.strip()) > 40

    def as_image_block(self) -> dict:
        """The page as an API image content block."""
        data = base64.standard_b64encode(self.png_path.read_bytes()).decode("utf-8")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": data},
        }


def render_pdf(pdf_path: Path, out_dir: Path, dpi: int | None = None) -> list[RenderedPage]:
    """Rasterise every page and pull its text layer."""
    dpi = dpi or settings.render_dpi
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[RenderedPage] = []

    with pymupdf.open(pdf_path) as doc:
        for index, page in enumerate(doc):
            png_path = out_dir / f"page-{index + 1:03d}.png"
            if not png_path.exists():
                page.get_pixmap(dpi=dpi).save(png_path)
            pages.append(
                RenderedPage(
                    page_number=index + 1,
                    png_path=png_path,
                    text=page.get_text("text"),
                    width_pt=page.rect.width,
                    height_pt=page.rect.height,
                )
            )
    return pages


def crop(
    pdf_path: Path,
    page_number: int,
    bbox: tuple[float, float, float, float],
    out_path: Path,
    dpi: int | None = None,
    padding_pt: float = 6.0,
) -> Path:
    """Render one region of one page to PNG.

    This is what students actually look at. Text extraction loses diagrams,
    graphs, circuit symbols and structural formulae — everything that makes a
    physics or chemistry question answerable — so the crop is the display
    artifact and the extracted text is only ever the search index.

    Storing the bbox rather than only the PNG means crops can be re-rendered at
    any resolution later (retina, print export) without re-running extraction.
    """
    dpi = dpi or settings.render_dpi
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with pymupdf.open(pdf_path) as doc:
        page = doc[page_number - 1]
        rect = pymupdf.Rect(*bbox) + (-padding_pt, -padding_pt, padding_pt, padding_pt)
        rect = rect & page.rect          # never crop outside the page
        page.get_pixmap(dpi=dpi, clip=rect).save(out_path)

    return out_path

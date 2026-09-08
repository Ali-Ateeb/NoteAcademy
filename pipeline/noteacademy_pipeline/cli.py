"""Pipeline entry points.

Deliberately a batch CLI rather than a service. Ingestion has no uptime
requirement, no request path and no users waiting on it — making it a FastAPI
app would add a deployment target that earns nothing. It runs, it writes to
Postgres, it exits.

    noteacademy render   paper.pdf --out work/5054-2019-mj-12
    noteacademy extract  work/5054-2019-mj-12 --pdf paper.pdf
    noteacademy mcq-key  ms.pdf
    noteacademy estimate --papers 3400 --pages 14
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import settings
from .render import crop, render_pdf

app = typer.Typer(add_completion=False, help="NoteAcademy past-paper ingestion.")
console = Console()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


@app.command()
def render(
    pdf: Path = typer.Argument(..., exists=True, help="Source PDF."),
    out: Path = typer.Option(..., help="Directory for rendered pages."),
    dpi: int = typer.Option(settings.render_dpi, help="Render resolution."),
) -> None:
    """Rasterise a PDF and report which pages carry a text layer."""
    pages = render_pdf(pdf, out, dpi=dpi)
    scanned = [p.page_number for p in pages if not p.has_text_layer]

    console.print(f"rendered [bold]{len(pages)}[/bold] pages at {dpi} dpi -> {out}")
    if scanned:
        console.print(
            f"[yellow]{len(scanned)} page(s) have no text layer "
            f"(vision only, no cross-check): {scanned}[/yellow]"
        )


@app.command()
def extract(
    work: Path = typer.Argument(..., exists=True, help="Directory of rendered pages."),
    pdf: Path = typer.Option(..., exists=True, help="Source PDF, for cropping."),
    out: Path = typer.Option(None, help="Where to write questions.json."),
) -> None:
    """Extract questions from rendered pages, cross-check them, and crop each one."""
    from .extract import cross_check, extract_page  # imported late: needs an API key

    pages = render_pdf(pdf, work)
    out = out or work / "questions.json"
    crops_dir = work / "crops"

    records: list[dict] = []
    flagged_pages: list[int] = []

    with console.status("extracting...") as status:
        for page in pages:
            status.update(f"extracting page {page.page_number}/{len(pages)}")
            extracted = extract_page(page)
            if not extracted.is_content_page:
                continue

            problems = cross_check(page, extracted)
            if problems:
                flagged_pages.append(page.page_number)
                for problem in problems:
                    console.print(f"[yellow]page {page.page_number}: {problem}[/yellow]")

            for question in extracted.questions:
                crop_path = crops_dir / f"{question.display_label.replace('/', '-')}.png"
                try:
                    crop(pdf, page.page_number, question.bbox.as_tuple(), crop_path)
                except Exception as exc:  # noqa: BLE001 - one bad bbox must not abort a paper
                    console.print(f"[red]crop failed for {question.display_label}: {exc}[/red]")
                    crop_path = None

                records.append(
                    {
                        **question.model_dump(),
                        "page_number": page.page_number,
                        "crop_path": str(crop_path) if crop_path else None,
                        "flagged": bool(problems),
                    }
                )

    out.write_text(json.dumps(records, indent=2))
    console.print(
        f"extracted [bold]{len(records)}[/bold] questions -> {out}"
        + (f"\n[yellow]{len(flagged_pages)} page(s) need review: {flagged_pages}[/yellow]"
           if flagged_pages else "")
    )


@app.command(name="mcq-key")
def mcq_key(
    ms_pdf: Path = typer.Argument(..., exists=True, help="MCQ mark scheme PDF."),
    out: Path = typer.Option(None, help="Where to write answers.json."),
    expect: int = typer.Option(40, help="Questions the paper should contain."),
    force_vision: bool = typer.Option(False, help="Skip the text parser."),
) -> None:
    """Read the answer grid out of an MCQ mark scheme.

    Tries the text layer first. CAIE multiple-choice mark schemes are a
    Question/Answer/Marks table that survives extraction intact, so the usual
    case costs nothing and cannot hallucinate an answer key — which matters more
    here than anywhere else in the pipeline, because a student trusts the key
    completely. Vision is the fallback for scanned or non-conforming papers.
    """
    import pymupdf

    from .markscheme import parse_mcq_answer_grid, validate_answer_grid

    work = settings.work_dir / ms_pdf.stem
    answers: dict[str, str] = {}
    source = "text layer"

    if not force_vision:
        with pymupdf.open(ms_pdf) as doc:
            answers = parse_mcq_answer_grid([page.get_text("text") for page in doc])
        problems = validate_answer_grid(answers, expect) if answers else ["no grid found"]
        if problems:
            console.print(f"[yellow]text layer: {'; '.join(problems)}[/yellow]")
            answers = {}

    if not answers:
        from .extract import extract_mcq_answers

        source = "vision"
        console.print("[yellow]falling back to the vision path[/yellow]")
        pages = render_pdf(ms_pdf, work)
        answers = extract_mcq_answers(pages)

    out = out or work / "answers.json"
    out.write_text(json.dumps(answers, indent=2))

    table = Table("Q", "Ans", title=f"{len(answers)} answers via {source}")
    for number in sorted(answers, key=lambda n: int(n) if n.isdigit() else 0):
        table.add_row(number, answers[number])
    console.print(table)
    console.print(f"-> {out}")


@app.command(name="segment-mcq")
def segment_mcq(
    qp_pdf: Path = typer.Argument(..., exists=True, help="Multiple-choice question paper."),
    out: Path = typer.Option(None, help="Directory for crops."),
    dpi: int = typer.Option(150, help="Crop resolution."),
) -> None:
    """Locate and crop every question in a multiple-choice paper.

    Geometric, not inferred: CAIE puts the question number alone in a left
    gutter, so boundaries are a fact about the page rather than something a
    model has to guess. No API call, and no possibility of an invented question.
    """
    from .segment import segment_mcq_paper

    regions, problems = segment_mcq_paper(qp_pdf)

    if problems:
        for problem in problems:
            console.print(f"[red]{problem}[/red]")
        console.print(
            "[yellow]Geometry does not match the multiple-choice template — "
            "send this paper down the vision path instead of trusting these "
            "boxes.[/yellow]"
        )
        raise typer.Exit(code=1)

    out = out or settings.work_dir / qp_pdf.stem / "crops"
    for region in regions:
        crop(
            qp_pdf,
            region.page_number,
            region.bbox,
            out / f"q{region.number:02d}.png",
            dpi=dpi,
        )

    console.print(
        f"segmented and cropped [bold]{len(regions)}[/bold] questions -> {out}"
    )


@app.command()
def estimate(
    papers: int = typer.Option(3400, help="Documents in the target corpus."),
    pages: int = typer.Option(14, help="Average pages per document."),
    input_per_page: int = typer.Option(2600, help="Input tokens per page, incl. image."),
    output_per_page: int = typer.Option(900, help="Output tokens per page."),
    cached_fraction: float = typer.Option(0.55, help="Share of input served from cache."),
) -> None:
    """Rough cost of a backfill, before you commit to one.

    Defaults describe roughly ten subjects across sixteen years. Measure on one
    real subject and re-run this with the numbers you actually observed — the
    point is the order of magnitude, not the third significant figure.
    """
    # claude-opus-5 list pricing, USD per million tokens.
    in_rate, out_rate, cache_read_rate = 5.00, 25.00, 0.50

    total_pages = papers * pages
    input_tokens = total_pages * input_per_page
    output_tokens = total_pages * output_per_page

    cached = input_tokens * cached_fraction
    uncached = input_tokens - cached
    cost = (
        uncached / 1e6 * in_rate
        + cached / 1e6 * cache_read_rate
        + output_tokens / 1e6 * out_rate
    )

    table = Table("", "", title="Extraction backfill estimate")
    table.add_row("documents", f"{papers:,}")
    table.add_row("pages", f"{total_pages:,}")
    table.add_row("input tokens", f"{input_tokens/1e6:,.1f}M")
    table.add_row("output tokens", f"{output_tokens/1e6:,.1f}M")
    table.add_row("[bold]extraction cost", f"[bold]${cost:,.0f}")
    console.print(table)
    console.print(
        "\n[dim]Multiple-choice papers cost nothing: their answer keys parse from "
        "the mark scheme's text layer and their questions segment geometrically "
        "(`mcq-key`, `segment-mcq`). This estimate covers the structured papers, "
        "which do need vision.[/dim]"
    )
    console.print(
        "[dim]Inference is not the constraint here — human review is. Budget for "
        "the review queue, not for tokens.[/dim]"
    )


if __name__ == "__main__":
    app()

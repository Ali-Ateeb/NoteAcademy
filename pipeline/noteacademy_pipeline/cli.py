"""Pipeline entry points.

Deliberately a batch CLI rather than a service. Ingestion has no uptime
requirement, no request path and no users waiting on it — making it a FastAPI
app would add a deployment target that earns nothing. It runs, it writes to
Postgres, it exits.

    noteacademy render   paper.pdf --out work/5054-2019-mj-12
    noteacademy extract  work/5054-2019-mj-12 --pdf paper.pdf
    noteacademy mcq-key  ms.pdf
    noteacademy load-mcq 5054_s19_qp_11.pdf 5054_s19_ms_11.pdf
    noteacademy load-syllabus 5054-2026-2028-syllabus.pdf
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


@app.command(name="load-mcq")
def load_mcq(
    qp_pdf: Path = typer.Argument(..., exists=True, help="Multiple-choice question paper."),
    ms_pdf: Path = typer.Argument(..., exists=True, help="Its mark scheme."),
    subject: str = typer.Option(None, help="Subject slug. Inferred from the syllabus code."),
    crops: Path = typer.Option(None, help="Also write the crops here, as PNG."),
    dpi: int = typer.Option(150, help="Crop resolution."),
    upload: bool = typer.Option(True, help="Upload the crops, if storage is set up."),
    dry_run: bool = typer.Option(False, help="Do the work, then roll it back."),
) -> None:
    """Load a multiple-choice sitting into the database. No model, no API key.

    Both files are named by Cambridge (5054_s19_qp_11.pdf), so the session,
    component and variant are read from the filenames rather than retyped as
    flags — across a backfill of thousands, a mistyped session files a paper
    under a year nobody will look in.

    Questions land unapproved, as everything from the pipeline does. They reach
    students when a human has signed them off in the review queue.
    """
    from .ingest import disagreements, ingest_mcq_paper, resolve_subject_slug
    from .load import connect
    from .naming import parse_paper_filename
    from .storage import StorageError, SupabaseStorage, upload_crops

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    try:
        qp = parse_paper_filename(qp_pdf.stem)
        ms = parse_paper_filename(ms_pdf.stem)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=2) from error

    mismatch = disagreements(qp, ms)
    if mismatch:
        console.print(
            f"[red]{qp_pdf.name} and {ms_pdf.name} disagree on: "
            f"{', '.join(mismatch)}[/red]"
        )
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        subject_slug = subject or resolve_subject_slug(conn, qp.syllabus_code)
        report = ingest_mcq_paper(
            conn,
            qp_pdf=qp_pdf,
            ms_pdf=ms_pdf,
            paper=qp,
            subject_slug=subject_slug,
            crops_dir=crops,
            crop_dpi=dpi,
        )

        if report.questions == 0:
            conn.rollback()
        elif dry_run:
            conn.rollback()
            console.print("[yellow]dry run: rolled back[/yellow]")
        else:
            conn.commit()

    if upload and not dry_run and report.crops:
        storage = SupabaseStorage.from_settings(settings)
        if storage is None:
            # Not an error: the questions are loaded and the crops are on disk.
            # Say what is missing rather than failing a run that mostly worked.
            console.print(
                "[yellow]storage is not configured, so the crops stay local — "
                "until they are uploaded these questions have nothing to "
                "display[/yellow]"
            )
        else:
            try:
                report.crops_uploaded = upload_crops(storage, report.crops)
            except StorageError as error:
                console.print(f"[red]upload failed: {error}[/red]")

    for problem in report.problems:
        console.print(f"[red]{problem}[/red]")

    if report.questions == 0:
        console.print(
            "[yellow]Nothing loaded. Geometry does not match the multiple-choice "
            "template — send this paper down the vision path instead.[/yellow]"
        )
        raise typer.Exit(code=1)

    table = Table("", "", title=report.paper_slug or subject_slug)
    table.add_row("questions", str(report.questions))
    table.add_row("with an answer", f"{report.with_answer}/{report.questions}")
    table.add_row("flagged for review", str(report.flagged))
    if crops is not None:
        table.add_row("crops written", f"{report.crops_written} -> {crops}")
    if report.crops_uploaded:
        table.add_row("crops uploaded", str(report.crops_uploaded))
    console.print(table)
    console.print(
        "[dim]Loaded unapproved. Nothing here is visible to a student until it "
        "is approved in the review queue.[/dim]"
    )

@app.command(name="load-syllabus")
def load_syllabus(
    pdf: Path = typer.Argument(..., exists=True, help="The published syllabus PDF."),
    subject: str = typer.Option(None, help="Subject slug. Inferred from the cover."),
    source_url: str = typer.Option(None, help="Where the PDF was published."),
    current: bool = typer.Option(True, help="Make this the subject's current version."),
    dry_run: bool = typer.Option(False, help="Parse and report, write nothing."),
) -> None:
    """Read a syllabus PDF into the topic tree.

    The topic tree is what tagging picks from, what the topical browser renders,
    and what carries a student across a syllabus revision. Transcribing it by
    hand is a day per subject and introduces the errors hardest to notice: a
    missing outcome is invisible, and a mistyped code silently detaches every
    question tagged with it.

    Refuses rather than guesses. If the parse does not match the template — a
    gap in the numbering, a topic with no outcomes, an equation whose fraction
    bar it could not resolve — it reports and stops.
    """
    from .ingest import resolve_subject_slug
    from .load import connect, upsert_syllabus_version, upsert_topic
    from .syllabus import parse_syllabus

    syllabus = parse_syllabus(pdf)

    table = Table("", "", title=f"{syllabus.subject_title} {syllabus.syllabus_code}")
    table.add_row("syllabus version", syllabus.label or "[red]not found[/red]")
    table.add_row("sections", str(len(syllabus.sections)))
    table.add_row("topics", str(sum(1 for _ in syllabus.walk())))
    table.add_row("learning objectives", str(syllabus.objective_count))
    table.add_row("equations resolved", f"{syllabus.fraction_bars - syllabus.unresolved_bars}"
                                        f"/{syllabus.fraction_bars}")
    console.print(table)

    for problem in syllabus.problems:
        console.print(f"[red]{problem}[/red]")
    if syllabus.problems:
        console.print(
            "[yellow]Not loaded. A topic tree that is nearly right is worse than "
            "none: it mistags the bank and sends students to the wrong unit.[/yellow]"
        )
        raise typer.Exit(code=1)

    if not syllabus.label or syllabus.first_exam_year is None:
        console.print("[red]the cover does not say which years this syllabus is for[/red]")
        raise typer.Exit(code=1)

    if dry_run:
        for topic in syllabus.walk():
            indent = "  " * topic.depth
            console.print(
                f"{indent}[bold]{topic.code}[/bold] {topic.title} "
                f"[dim]({len(topic.learning_objectives)})[/dim]"
            )
        return

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        subject_slug = subject or resolve_subject_slug(conn, syllabus.syllabus_code)
        version_id = upsert_syllabus_version(
            conn,
            subject_slug,
            label=syllabus.label,
            first_exam_year=syllabus.first_exam_year,
            last_exam_year=syllabus.last_exam_year,
            source_url=source_url,
            is_current=current,
        )

        # Parents before children, so a child always has a parent to point at.
        ids: dict[str, str] = {}
        for order, topic in enumerate(syllabus.walk()):
            parent_code = topic.code.rpartition(".")[0]
            ids[topic.code] = upsert_topic(
                conn,
                version_id,
                code=topic.code,
                title=topic.title,
                slug=topic.slug,
                learning_objectives=topic.learning_objectives,
                parent_topic_id=ids.get(parent_code),
                sort_order=order,
            )
        conn.commit()

    console.print(
        f"loaded [bold]{len(ids)}[/bold] topics into {subject_slug} "
        f"{syllabus.label}" + (" [dim](now the current version)[/dim]" if current else "")
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

"""Pipeline entry points.

Deliberately a batch CLI rather than a service. Ingestion has no uptime
requirement, no request path and no users waiting on it — making it a FastAPI
app would add a deployment target that earns nothing. It runs, it writes to
Postgres, it exits.

    noteacademy render   paper.pdf --out work/5054-2019-mj-12
    noteacademy extract  work/5054-2019-mj-12 --pdf paper.pdf
    noteacademy mcq-key  ms.pdf
    noteacademy load-mcq 5054_s19_qp_11.pdf 5054_s19_ms_11.pdf
    noteacademy load-structured 5054_s15_qp_21.pdf 5054_s15_ms_21.pdf
    noteacademy load-syllabus 5054-2026-2028-syllabus.pdf
    noteacademy estimate --papers 3400 --pages 14
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import settings
from .deepseek import DeepSeekAccountError
from .render import crop, render_pdf

# Windows' legacy console defaults stdout to the system codepage (cp1252),
# which cannot encode most of what a real syllabus contains — the reversible-
# reaction arrow (⇌), degree signs, em dashes. `reconfigure` is a no-op on a
# stream that is already UTF-8 (Linux, macOS, a modern Windows Terminal), so
# this only ever helps; `sys.stdout` can lack the method entirely when it has
# been replaced by a non-standard stream, hence the guard.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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


@app.command(name="mcq-options")
def mcq_options(
    qp_pdf: Path = typer.Argument(
        ..., exists=True, help="Multiple-choice question paper, already loaded."
    ),
    dry_run: bool = typer.Option(False, help="Do the work, then roll it back."),
) -> None:
    """Backfill question and option text for an already-loaded MCQ paper.

    `load-mcq` files a question from geometry alone — a crop, a number, an
    answer — because CAIE's reading order is scrambled and some options are
    diagrams. This calls the vision pass on the same paper and writes what it
    finds onto the existing rows.

    Costs at most one API call per page still needing one: a question already
    backfilled is skipped, a marked duplicate of one is copied for free, and a
    page whose every question falls into one of those two buckets is never
    sent to the model. Run a sitting's variants in any order — the second one
    processed is the cheap one, since it shares most of its MCQs with the
    first.
    """
    from .mcq_options import backfill_paper_options

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    try:
        # Manages its own connections: it holds none while the vision model is
        # being called, so nothing sits idle in transaction across a slow call.
        report = backfill_paper_options(qp_pdf, dry_run=dry_run)
    except LookupError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=2) from error

    if dry_run:
        console.print("[yellow]dry run: nothing was written[/yellow]")

    table = Table("", "", title=report.paper_slug)
    table.add_row("questions matched", str(report.matched))
    table.add_row("options written", str(report.written))
    table.add_row("copied from a duplicate (no API call)", str(report.copied_from_duplicate))
    table.add_row("pages skipped (fully resolved)", str(report.pages_skipped))
    console.print(table)

    if report.unmatched_labels:
        console.print(
            f"[red]{len(report.unmatched_labels)} label(s) had no matching question: "
            f"{report.unmatched_labels}[/red]"
        )
    if report.figure_options:
        console.print(
            f"[yellow]{len(report.figure_options)} question(s) have at least one "
            f"figure option, described rather than transcribed: {report.figure_options}[/yellow]"
        )
    if report.flagged_pages:
        console.print(
            f"[yellow]{len(report.flagged_pages)} page(s) failed cross-check, "
            f"worth a manual look: {report.flagged_pages}[/yellow]"
        )


@app.command(name="load-structured")
def load_structured(
    qp_pdf: Path = typer.Argument(..., exists=True, help="Structured (Paper 2) question paper."),
    ms_pdf: Path = typer.Argument(..., exists=True, help="Its mark scheme."),
    subject: str = typer.Option(None, help="Subject slug. Inferred from the syllabus code."),
    crops: Path = typer.Option(None, help="Also write the crops here, as PNG."),
    dpi: int = typer.Option(150, help="Crop resolution."),
    upload: bool = typer.Option(True, help="Upload the crops, if storage is set up."),
    dry_run: bool = typer.Option(False, help="Do the work, then roll it back."),
) -> None:
    """Load a structured sitting into the database. No model, no API key.

    Boundaries come from each label's left indent, not a vision pass, so this
    only understands papers that follow CAIE's usual question/part/sub-part
    layout. Everything lands unapproved, the same as `load-mcq`.
    """
    from .ingest import disagreements, ingest_structured_paper, resolve_subject_slug
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
        report = ingest_structured_paper(
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
            "[yellow]Nothing loaded. Geometry does not match the structured "
            "template — this paper needs a person, not this path.[/yellow]"
        )
        raise typer.Exit(code=1)

    table = Table("", "", title=report.paper_slug or subject_slug)
    table.add_row("questions and parts", str(report.questions))
    table.add_row("matched to mark scheme", str(report.with_answer))
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

@app.command(name="tag-export")
def tag_export(
    subject: str = typer.Option("physics-5054", help="Subject slug to tag."),
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    out: Path = typer.Option(None, help="Where to write the worksheet JSON."),
    paper: str = typer.Option(None, help="Restrict to one paper slug."),
    limit: int = typer.Option(None, help="Stop after this many questions."),
    retag: bool = typer.Option(False, help="Include questions that already have a topic."),
    structured: bool = typer.Option(
        False, help="Tag structured questions instead of mcq. Needs no source PDF."
    ),
) -> None:
    """Export the questions that still need a topic, with their text.

    The worksheet carries the closed syllabus list and one entry per question,
    so whatever does the classifying — the API, a person, a Claude Code session
    with no API key — is working from the same fixed set of codes. Feed the
    decisions back with `tag-apply`.
    """
    import json

    from .load import connect
    from .worksheet import build_structured_worksheet, build_worksheet

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        sheet = (
            build_structured_worksheet(
                conn, subject, paper_slug=paper, limit=limit, include_tagged=retag
            )
            if structured
            else build_worksheet(
                conn, subject, papers, paper_slug=paper, limit=limit, include_tagged=retag
            )
        )

    out = out or settings.work_dir / f"{subject}-tags.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sheet.as_dict(), indent=2, ensure_ascii=False), "utf-8")

    sparse = sum(1 for question in sheet.questions if question.is_sparse)
    table = Table("", "", title=f"{subject} {sheet.syllabus_label}")
    table.add_row("topics to choose from", str(len(sheet.topics)))
    table.add_row("questions needing a topic", str(len(sheet.questions)))
    table.add_row("too little text to classify", str(sparse))
    console.print(table)

    for missing in sheet.missing_papers:
        console.print(f"[yellow]no source PDF for {missing} — its questions were skipped[/yellow]")
    if sparse:
        console.print(
            "[dim]Questions marked `sparse` are mostly artwork — circuit diagrams "
            "as options, graphs with no caption. Their crop says what their text "
            "cannot; send those to a person.[/dim]"
        )
    console.print(f"-> {out}")


@app.command(name="tag-apply")
def tag_apply(
    decisions: Path = typer.Argument(..., exists=True, help="JSON list of decisions."),
    subject: str = typer.Option("physics-5054", help="Subject the decisions belong to."),
    floor: float = typer.Option(settings.tag_confidence_floor,
                                help="Below this confidence, hold the question back."),
    dry_run: bool = typer.Option(False, help="Do the work, then roll it back."),
) -> None:
    """Write topic decisions back to the database.

    Each entry is {"id": ..., "topic_code": ..., "confidence": 0.0-1.0}. A code
    outside the syllabus is dropped rather than written — the closed list is the
    guarantee, and a classifier returning something not on it has said it was
    guessing.
    """
    import json

    from .load import connect
    from .worksheet import apply_worksheet

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    payload = json.loads(decisions.read_text(encoding="utf-8"))
    entries = payload["decisions"] if isinstance(payload, dict) else payload

    with connect(settings.database_url) as conn:
        report = apply_worksheet(conn, subject, entries, confidence_floor=floor)
        if dry_run:
            conn.rollback()
            console.print("[yellow]dry run: rolled back[/yellow]")
        else:
            conn.commit()

    table = Table("", "", title="Topics written")
    table.add_row("tagged", str(report.tagged))
    table.add_row(f"held for review (< {floor})", str(report.flagged))
    console.print(table)

    if report.unknown_codes:
        console.print(
            f"[red]{len(report.unknown_codes)} decision(s) named a code outside the "
            f"syllabus and were dropped: {sorted(set(report.unknown_codes))[:5]}[/red]"
        )
    if report.unknown_questions:
        console.print(
            f"[red]{len(report.unknown_questions)} decision(s) named a question that "
            "does not exist[/red]"
        )


@app.command(name="tag-verify-export")
def tag_verify_export(
    subject: str = typer.Option("physics-5054", help="Subject to build sheets for."),
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    out: Path = typer.Option(Path("pipeline/work/verify"), help="Output directory."),
    target_width: int = typer.Option(700, help="Crop width in the contact sheets."),
    max_sheet_height: int = typer.Option(5000, help="Roughly how tall one sheet gets."),
) -> None:
    """Build contact sheets for a second, independent tagging pass.

    Every already-tagged question's crop is laid out with its current topic
    withheld, duplicate questions across paper variants folded to one copy, and
    a manifest written mapping each sheet position back to the question(s) it
    stands for. Look at the sheets, decide a topic per position, and feed the
    result to `tag-verify-apply`.
    """
    from .load import connect
    from .verify import build_verification_sheets

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        report = build_verification_sheets(
            conn, subject, papers, out,
            target_width=target_width, max_sheet_height=max_sheet_height,
        )

    table = Table("", "", title=f"{subject} — second-pass worksheet")
    table.add_row("questions shown", str(report.items_shown))
    table.add_row("duplicates folded away", str(report.duplicates_folded))
    table.add_row("sheets written", str(len(report.sheets)))
    console.print(table)

    if report.missing_crop:
        console.print(
            f"[yellow]{len(report.missing_crop)} question(s) have no local crop "
            "and were skipped — re-run load-mcq with --crops for that paper.[/yellow]"
        )
    console.print(f"-> {out / 'manifest.json'}  +  {out / 'sheets'}")


@app.command(name="tag-verify-apply")
def tag_verify_apply(
    decisions: Path = typer.Argument(..., exists=True, help="JSON {tag: topic_code}."),
    manifest: Path = typer.Option(
        Path("pipeline/work/verify/manifest.json"), exists=True,
        help="The manifest tag-verify-export wrote.",
    ),
    floor: float = typer.Option(settings.tag_confidence_floor,
                                help="Below this confidence, hold the question back."),
    dry_run: bool = typer.Option(False, help="Do the work, then roll it back."),
) -> None:
    """Apply a second pass's decisions against the first.

    Agreement raises the tag's confidence. Disagreement records the second
    opinion as a secondary topic and drops the question below the review
    floor — the same mechanism the first pass uses for "unsure", because two
    independent methods landing on different topics *is* unsure.
    """
    import json

    from .load import connect
    from .verify import apply_verification

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    decisions_data = json.loads(decisions.read_text(encoding="utf-8"))

    with connect(settings.database_url) as conn:
        report = apply_verification(
            conn, manifest_data, decisions_data, confidence_floor=floor
        )
        if dry_run:
            conn.rollback()
            console.print("[yellow]dry run: rolled back[/yellow]")
        else:
            conn.commit()

    table = Table("", "", title="Second pass applied")
    table.add_row("agreed (confidence raised)", str(report.agreed))
    table.add_row("disagreed (sent to review)", str(report.disagreed))
    table.add_row("already approved, left alone", str(report.skipped_approved))
    table.add_row("no decision given", str(report.skipped_no_decision))
    console.print(table)

    if report.unknown_codes:
        console.print(
            f"[red]{len(report.unknown_codes)} decision(s) named a code outside "
            f"the syllabus and were dropped: {sorted(set(report.unknown_codes))[:5]}[/red]"
        )


@app.command(name="tag-auto")
def tag_auto(
    subject: str = typer.Option("physics-5054", help="Subject to tag."),
    floor: float = typer.Option(settings.tag_confidence_floor,
                                help="Below this confidence, hold the question back."),
    dry_run: bool = typer.Option(True, help="Report what would happen, without writing it."),
    from_year: int = typer.Option(settings.scope_year_from, help="First sitting year to tag."),
    to_year: int = typer.Option(settings.scope_year_to, help="Last sitting year to tag."),
    paper: str = typer.Option(None, help="Restrict to one paper slug."),
    limit: int = typer.Option(None, help="Stop after this many questions."),
    workers: int = typer.Option(8, help="Model calls in flight at once (1 = one at a time)."),
    retag: bool = typer.Option(
        False, help="Replace the topics of questions that already have one, and put them back "
                    "into review. Needs --paper; the old tags are saved to a CSV first."),
    from_crops: bool = typer.Option(
        False, "--from-crops",
        help="Tag the questions this command otherwise skips -- the ones whose stem and options "
             "are all artwork -- by reading their printed crop with the vision model."),
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live (--from-crops)."),
) -> None:
    """First-pass topic tags for multiple-choice questions, read from text.

    Takes each untagged MCQ's stem, options and keyed answer (put there by
    `mcq-options`) and asks the text model for a topic from the syllabus's
    closed list. Nothing is approved: a tag below the confidence floor is
    held for review, and `tag-verify-auto` then gives every tag an
    independent second read from the printed crop. Questions whose text has
    not been read yet are skipped, not guessed at.
    """
    from .tag_auto import tag_mcqs_from_crops, tag_mcqs_with_model

    _require_verify_setup(from_year, to_year)

    if from_crops:
        label = f"reading crops for {subject} {from_year}-{to_year}"
        with console.status(label + "...") as status:
            try:
                result = tag_mcqs_from_crops(
                    subject, papers, year_from=from_year, year_to=to_year,
                    confidence_floor=floor, dry_run=dry_run, workers=workers,
                    paper_slug=paper,
                    on_progress=lambda done, total: status.update(f"{label}: {done}/{total}"),
                )
            except DeepSeekAccountError as error:
                console.print(f"[red]{error}. Nothing was written.[/red]")
                raise typer.Exit(code=2) from error

        table = Table("", "", title=f"{subject} - tagged from the printed crop"
                                    + (" (dry run)" if dry_run else ""))
        table.add_row("untagged questions with no text", str(result.candidates))
        table.add_row("tagged", str(result.tagged))
        table.add_row("no crop recorded, skipped", str(result.skipped_no_crop))
        table.add_row("no source PDF, skipped", str(result.skipped_no_pdf))
        table.add_row("call failed, skipped", str(len(result.failed_calls)))
        console.print(table)
        if result.by_topic:
            console.print("topics: " + ", ".join(
                f"{c} x{n}" for c, n in sorted(result.by_topic.items())))
        console.print(
            "[yellow]These are held below the confidence floor on purpose: one read of a "
            "picture, with no text and no second opinion, is a lead for a person.[/yellow]"
        )
        if dry_run:
            console.print("[yellow]dry run: nothing was written.[/yellow]")
        return

    label = f"tagging {subject} {from_year}-{to_year} with {settings.deepseek_text_model}"
    with console.status(label + "...") as status:
        try:
            report = tag_mcqs_with_model(
                subject, year_from=from_year, year_to=to_year, confidence_floor=floor,
                dry_run=dry_run, workers=workers, paper_slug=paper, limit=limit, retag=retag,
                on_progress=lambda done, total: status.update(f"{label}: {done}/{total}"),
            )
        except DeepSeekAccountError as error:
            console.print(f"[red]{error}. Nothing was written.[/red]")
            raise typer.Exit(code=2) from error

    table = Table("", "", title=f"{subject} - first-pass tags" + (" (dry run)" if dry_run else ""))
    table.add_row("untagged questions in range", str(report.candidates))
    table.add_row("tagged", str(report.tagged))
    table.add_row(f"  of which held for review (< {floor})", str(report.held_for_review))
    table.add_row("no text read yet, skipped", str(report.skipped_no_text))
    table.add_row("call failed, skipped", str(len(report.failed_calls)))
    console.print(table)

    if retag:
        console.print(
            f"retagged {report.retagged}, of which the topic changed {report.changed_topic}; "
            f"all returned to review. Old tags: {report.backup}"
        )
    if report.by_topic:
        spread = Table("topic", "questions", title="Spread across the syllabus")
        for code, count in sorted(report.by_topic.items()):
            spread.add_row(code, str(count))
        console.print(spread)
    if report.unknown_codes:
        console.print(
            f"[red]{len(report.unknown_codes)} response(s) named a topic outside the "
            f"syllabus and were dropped: {sorted(set(report.unknown_codes))[:5]}[/red]"
        )
    if report.failed_calls:
        console.print(
            f"[yellow]{len(report.failed_calls)} call(s) failed, worth a re-run: "
            f"{report.failed_calls[:5]}[/yellow]"
        )
    if report.skipped_no_text:
        console.print("[yellow]Run `mcq-options` on those papers first.[/yellow]")
    if dry_run:
        console.print(
            "[yellow]dry run: nothing was written. Re-run with --no-dry-run to apply.[/yellow]"
        )


@app.command(name="tag-verify-auto")
def tag_verify_auto(
    subject: str = typer.Option("physics-5054", help="Subject to verify."),
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    floor: float = typer.Option(settings.tag_confidence_floor,
                                help="Below this confidence, hold the question back."),
    dry_run: bool = typer.Option(True, help="Report what would happen, without writing it."),
    from_year: int = typer.Option(settings.scope_year_from, help="First sitting year to verify."),
    to_year: int = typer.Option(settings.scope_year_to, help="Last sitting year to verify."),
    thinking: bool = typer.Option(
        True, "--thinking/--no-thinking",
        help="DeepSeek thinking mode. On by default here: it fixed both borderline cases "
             "it was tried on, for about a tenth of a cent a question.",
    ),
    workers: int = typer.Option(8, help="Model calls in flight at once (1 = one at a time)."),
    from_results: Path = typer.Option(
        None, "--from-results", exists=True,
        help="Replay the model answers saved by an earlier run instead of calling the model "
             "again. For when the calls succeeded but the write pass did not.",
    ),
) -> None:
    """The automated counterpart to tag-verify-export/tag-verify-apply: a
    vision-capable model reads each MCQ's own printed crop and chooses a
    topic, compared against the first pass exactly like a person's decision
    from a contact sheet already is. DeepSeek only.

    Reads the crop, not the extracted text the first pass used — a text-only
    second opinion would be a second classifier reading the same scrambled
    words, not independent evidence. Defaults to a dry run: this can move a
    question's tag confidence and review status across the whole subject in
    one call.

    Only sittings from --from-year to --to-year are read (default: the
    pipeline's current scope, NOTEACADEMY_YEAR_FROM/TO).

    The model's answers are saved to pipeline/work/verify-mcq-<subject>-
    results.jsonl before anything is written, and each question commits on its
    own (reconnecting if the link drops), so a failed write costs a retry of
    the writes rather than of the model calls -- `--from-results` is that
    retry. Approved questions the model disagreed with are listed in a CSV,
    since they are reported and never edited.
    """
    import psycopg

    from .verify import verify_mcqs_with_model
    from .verify_write import resume_mcqs_from_results, write_approved_disagreements_csv

    if from_results is not None:
        # A replay needs the database, but no key and no year range: the
        # questions it touches are whichever ones the saved file names.
        if not settings.database_url:
            console.print("[red]DATABASE_URL is not set.[/red]")
            raise typer.Exit(code=2)
        report = resume_mcqs_from_results(
            subject, from_results, confidence_floor=floor, dry_run=dry_run
        )
        title = f"{subject} — replayed from {from_results.name}"
    else:
        _require_verify_setup(from_year, to_year)

        label = (
            f"verifying {subject} {from_year}-{to_year} against {settings.deepseek_vision_model}"
        )
        with console.status(label + "...") as status:
            try:
                report = verify_mcqs_with_model(
                    subject, papers, confidence_floor=floor, dry_run=dry_run,
                    year_from=from_year, year_to=to_year, thinking=thinking, workers=workers,
                    on_progress=lambda done, total: status.update(f"{label}: {done}/{total}"),
                )
            except DeepSeekAccountError as error:
                console.print(f"[red]{error}. Nothing was written.[/red]")
                raise typer.Exit(code=2) from error
        title = f"{subject} — automated second pass"
    if dry_run:
        console.print("[yellow]dry run: rolled back[/yellow]")

    table = Table("", "", title=title + (" (dry run)" if dry_run else ""))
    table.add_row("agreed (confidence raised)", str(report.agreed))
    table.add_row("disagreed (sent to review)", str(report.disagreed))
    table.add_row("already approved (agreed or not), not edited", str(report.skipped_approved))
    table.add_row("  of which the model disagreed, reported only", str(report.disagreed_approved))
    table.add_row("no crop available, skipped", str(report.skipped_no_decision))
    table.add_row("call failed, skipped", str(len(report.failed_calls)))
    table.add_row("locked by another session, skipped", str(report.skipped_locked))
    table.add_row("write failed", str(len(report.failed_writes)))
    table.add_row("connection re-opened", str(report.reconnects))
    console.print(table)

    if report.results_path and from_results is None:
        console.print(f"model answers saved: {report.results_path}")
    try:
        disagreements = write_approved_disagreements_csv(
            report, settings.work_dir / f"verify-mcq-{subject}-approved-disagreements.csv"
        )
    except psycopg.Error as error:
        console.print(f"[yellow]could not write the disagreements list: {error}[/yellow]")
        disagreements = None
    if disagreements:
        console.print(
            f"approved questions the model disagreed with ({len(report.approved_disagreements)}): "
            f"{disagreements}"
        )
    if report.failed_writes or report.skipped_locked:
        console.print(
            "[yellow]Some groups were not written. Re-run with "
            f"--from-results {report.results_path} to pick them up -- each question is "
            "compared against its current tag, so ones that already landed are harmless."
            "[/yellow]"
        )

    if report.unknown_codes:
        console.print(
            f"[red]{len(report.unknown_codes)} response(s) returned an unknown/invalid "
            f"topic code: {sorted(set(report.unknown_codes))[:5]}[/red]"
        )
    if report.failed_calls:
        console.print(
            f"[yellow]{len(report.failed_calls)} question(s) the model call itself failed "
            f"on, worth a re-run: {report.failed_calls[:5]}[/yellow]"
        )
    if dry_run:
        console.print(
            "[yellow]dry run: nothing was written. Re-run with --no-dry-run to apply.[/yellow]"
        )


def _require_verify_setup(from_year: int, to_year: int) -> None:
    """The preconditions both automated verifiers share."""
    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)
    if not settings.deepseek_api_key:
        console.print("[red]DEEPSEEK_API_KEY is not set.[/red]")
        raise typer.Exit(code=2)
    if from_year > to_year:
        console.print(f"[red]--from-year {from_year} is after --to-year {to_year}.[/red]")
        raise typer.Exit(code=2)


@app.command(name="tag-verify-structured")
def tag_verify_structured(
    subject: str = typer.Option("physics-5054", help="Subject to verify."),
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    floor: float = typer.Option(settings.tag_confidence_floor,
                                help="Below this confidence, hold the question back."),
    dry_run: bool = typer.Option(True, help="Report what would happen, without writing it."),
    from_year: int = typer.Option(settings.scope_year_from, help="First sitting year to verify."),
    to_year: int = typer.Option(settings.scope_year_to, help="Last sitting year to verify."),
    paper: list[str] = typer.Option(
        None, "--paper",
        help="Only this paper slug (repeat for several), e.g. chemistry-5070-2020-may-june-p21.",
    ),
    thinking: bool = typer.Option(
        False, "--thinking/--no-thinking",
        help="DeepSeek thinking mode. OFF by default here, unlike tag-verify-auto: on a "
             "20-question trial it was ~4x slower, changed the verdict on ~25% of questions "
             "between identical runs, and over-weighted whichever part carried the most marks.",
    ),
    workers: int = typer.Option(8, help="Model calls in flight at once (1 = one at a time)."),
    from_results: Path = typer.Option(
        None, "--from-results", exists=True,
        help="Replay the model answers saved by an earlier run instead of calling the model "
             "again. For when the calls succeeded but the write pass did not.",
    ),
    report_path: Path = typer.Option(
        None, "--report",
        help="Where to write the triage CSV (default: pipeline/work/verify-structured-*.csv).",
    ),
    verbose: bool = typer.Option(False, help="Print every question's result, not just conflicts."),
) -> None:
    """The structured counterpart to tag-verify-auto: a vision model reads each
    structured question's printed page-crops (every page, in order) and chooses
    a primary topic and up to two secondary ones, compared against the first
    pass, which worked from extracted text. DeepSeek only.

    A structured question usually tests several topics, so two reads can list
    the same topics in a different order. That is reported as 'reordered' and
    changes nothing; only a read with no topic in common is a 'disagreement'.
    A disagreement on a question that is not yet approved returns it to the
    review queue. On one that is ALREADY approved it only lowers the tag's
    confidence and appears in the report — an approved question is live to
    students, and a model's second opinion should not edit it unreviewed.

    Defaults to a dry run. Every result, disagreements first, is written to a
    CSV for triage.
    """
    from datetime import datetime

    from .verify_structured import verify_structured_with_model, write_findings_csv

    if from_results is not None:
        # A replay needs the database, but no key and no year range: the
        # questions it touches are whichever ones the saved file names.
        from .verify_structured import resume_structured_from_results

        if not settings.database_url:
            console.print("[red]DATABASE_URL is not set.[/red]")
            raise typer.Exit(code=2)
        result = resume_structured_from_results(
            subject, from_results, confidence_floor=floor, dry_run=dry_run
        )
        table = Table("", "", title=f"{subject} — replayed from {from_results.name}")
        table.add_row("agreed (confidence raised)", str(result.agreed))
        table.add_row("reordered (nothing written)", str(result.reordered))
        table.add_row("disagreed", str(result.disagreed))
        table.add_row("  of those, approved and left alone", str(result.disagreed_approved))
        table.add_row("gone or changed since the run", str(result.skipped_changed))
        table.add_row("locked by another session, skipped", str(result.skipped_locked))
        table.add_row("write failed", str(len(result.failed_writes)))
        table.add_row("connection re-opened", str(result.reconnects))
        console.print(table)
        if result.findings:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = report_path or settings.work_dir / f"verify-structured-{subject}-{stamp}.csv"
            write_findings_csv(result.findings, path)
            console.print(f"triage report: {path}")
        if result.skipped_locked or result.failed_writes:
            console.print(
                "[yellow]Some rows were not written (held by another session, or the "
                "database was unreachable). Re-run this same command to pick them "
                "up; ones that already landed are harmless.[/yellow]"
            )
        if dry_run:
            console.print("[yellow]dry run: rolled back[/yellow]")
        return

    _require_verify_setup(from_year, to_year)

    label = (
        f"verifying {subject} {from_year}-{to_year} structured "
        f"against {settings.deepseek_vision_model}"
    )
    with console.status(label + "...") as status:
        try:
            report = verify_structured_with_model(
                subject, papers, year_from=from_year, year_to=to_year,
                confidence_floor=floor, dry_run=dry_run, thinking=thinking,
                workers=workers, paper_slugs=paper or None,
                on_progress=lambda done, total: status.update(f"{label}: {done}/{total}"),
            )
        except DeepSeekAccountError as error:
            console.print(f"[red]{error}. Nothing was written.[/red]")
            raise typer.Exit(code=2) from error

    table = Table(
        "", "", title=f"{subject} {from_year}-{to_year} — structured second pass"
        + (" (dry run)" if dry_run else "")
    )
    table.add_row("agreed (confidence raised)", str(report.agreed))
    table.add_row("reordered (same topics, other order; nothing written)", str(report.reordered))
    table.add_row("disagreed (no topic in common)", str(report.disagreed))
    table.add_row("   of which already approved: reported only", str(report.disagreed_approved))
    table.add_row("   of which returned to the review queue",
                  str(report.disagreed - report.disagreed_approved))
    table.add_row("no crops on file, skipped", str(report.skipped_no_crops))
    table.add_row("too many page crops, skipped", str(report.skipped_too_many_pages))
    table.add_row("source PDF missing, skipped", str(report.skipped_no_pdf))
    table.add_row("changed while running, skipped", str(report.skipped_changed))
    table.add_row("call failed, skipped", str(len(report.failed_calls)))
    table.add_row("locked by another session, skipped", str(report.skipped_locked))
    table.add_row("write failed", str(len(report.failed_writes)))
    table.add_row("connection re-opened", str(report.reconnects))
    console.print(table)
    if report.results_path:
        console.print(f"model answers saved: {report.results_path}")
    if report.failed_writes or report.skipped_locked:
        console.print(
            "[yellow]Some questions were not written. Re-run with "
            f"--from-results {report.results_path} to pick them up; ones that already "
            "landed are harmless.[/yellow]"
        )

    shown = [f for f in report.findings if verbose or f.outcome == "disagreed"]
    for f in shown:
        colour = {"agreed": "green", "reordered": "yellow", "disagreed": "red"}[f.outcome]
        plural = "s" if f.pages != 1 else ""
        file_extra = f" + {' '.join(f.file_secondary)}" if f.file_secondary else ""
        model_extra = f" + {' '.join(f.model_secondary)}" if f.model_secondary else ""
        console.print(
            f"[{colour}]{f.outcome.upper():9s}[/{colour}] {f.paper_slug} Q{f.display_label} "
            f"({f.status}, {f.pages} page{plural})"
        )
        console.print(f"    file  : {f.file_primary} ({f.file_confidence:.2f}){file_extra}")
        console.print(f"    model : {f.model_primary} ({f.model_confidence:.2f}){model_extra}")
        console.print(f"    why   : {f.reasoning}", highlight=False)

    if report.findings:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = report_path or settings.work_dir / f"verify-structured-{subject}-{stamp}.csv"
        write_findings_csv(report.findings, path)
        console.print(f"triage report: {path}")

    if report.unknown_codes:
        console.print(
            f"[red]{len(report.unknown_codes)} response(s) returned an unknown/invalid "
            f"topic code: {sorted(set(report.unknown_codes))[:5]}[/red]"
        )
    if report.failed_calls:
        console.print(
            f"[yellow]{len(report.failed_calls)} question(s) the model call itself failed "
            f"on, worth a re-run: {report.failed_calls[:5]}[/yellow]"
        )
    if dry_run:
        console.print(
            "[yellow]dry run: nothing was written. Re-run with --no-dry-run to apply.[/yellow]"
        )


@app.command()
def dedupe(
    subject: str = typer.Option("physics-5054", help="Subject slug to deduplicate."),
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    dry_run: bool = typer.Option(False, help="Do the work, then roll it back."),
) -> None:
    """Mark verbatim-duplicate MCQs shared across a subject's paper variants.

    CAIE reuses most multiple-choice items between one session's variants —
    component 11 and 12 of the same sitting, most often. Marking the repeats
    here, once, means the topical browser and drills can fold them without
    re-deriving the match, and a student can be told a question is one CAIE
    actually reuses rather than seeing it twice with no explanation.
    """
    from .load import connect
    from .verify import apply_dedupe

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        report = apply_dedupe(conn, subject, papers, dry_run=dry_run)
        if dry_run:
            conn.rollback()
            console.print("[yellow]dry run: rolled back[/yellow]")
        else:
            conn.commit()

    table = Table("", "", title="Duplicates marked")
    table.add_row("groups with a duplicate", str(report.groups_with_duplicates))
    table.add_row("questions marked as a duplicate", str(report.duplicates_marked))
    console.print(table)


@app.command(name="bulk-approve")
def bulk_approve_cmd(
    subject: str = typer.Option("physics-5054", help="Subject slug to approve within."),
    floor: float = typer.Option(
        settings.tag_confidence_floor,
        help="Only approve a question whose primary topic is at least this confident.",
    ),
    dry_run: bool = typer.Option(
        True, help="List how many would be approved, without approving them."
    ),
) -> None:
    """Approve every MCQ whose primary topic is confidently, unflaggedly tagged.

    This is the lever for a review queue that is mostly agreement: an answer
    key parsed from a machine-readable table and a geometrically segmented
    crop need no human, so once a topic tag is trusted — confident, and not
    sitting under any other review flag — approving it by hand adds nothing a
    script could not already tell. Defaults to a dry run: approving publishes
    a question to students, and this can move hundreds of rows in one call.
    """
    from .load import connect
    from .verify import bulk_approve

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        report = bulk_approve(conn, subject, confidence_floor=floor, dry_run=dry_run)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    table = Table("", "", title="Bulk approve" + (" (dry run)" if dry_run else ""))
    table.add_row("subject", report.subject)
    table.add_row("confidence floor", f"{report.floor:.2f}")
    table.add_row("candidates", str(report.candidates))
    table.add_row("approved", str(report.approved))
    console.print(table)
    if dry_run:
        console.print(
            "[yellow]dry run: nothing was written. Re-run with --no-dry-run to approve.[/yellow]"
        )


@app.command(name="bulk-approve-structured")
def bulk_approve_structured_cmd(
    subject: str = typer.Option("physics-5054", help="Subject slug to approve within."),
    dry_run: bool = typer.Option(
        True, help="List how many would be approved, without approving them."
    ),
) -> None:
    """Approve every structured question whose whole subtree is flag-free.

    No topic-confidence floor, unlike `bulk-approve`: a structured question's
    content is already vetted by the same refuse-rather-than-guess pairing an
    mcq's is (geometry that either matches the template or refuses the whole
    paper; a mark scheme matched only on an exact label, never a guess), and
    a topic tag is a separate concern from whether that content can be
    trusted. Defaults to a dry run: approving publishes a paper's questions
    to students, and one call can move hundreds of rows across many papers.
    """
    from .load import connect
    from .verify import bulk_approve_structured

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        report = bulk_approve_structured(conn, subject, dry_run=dry_run)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    table = Table("", "", title="Bulk approve structured" + (" (dry run)" if dry_run else ""))
    table.add_row("subject", report.subject)
    table.add_row("candidate questions", str(report.candidates))
    table.add_row("rows moved (incl. parts)", str(report.questions_moved))
    table.add_row("approved", str(report.approved))
    table.add_row(f"  tagged below the floor ({settings.tag_confidence_floor})",
                  str(report.below_floor))
    table.add_row("  not tagged at all", str(report.untagged))
    console.print(table)

    if report.below_floor or report.untagged:
        # This command vets the content, never the tag (see bulk_approve_structured).
        # Saying so out loud: approving publishes the tag too, and a question filed
        # under a topic the classifier was unsure of is served to students revising
        # that topic. 451 chemistry questions reached the site this way.
        console.print(
            f"[yellow]{report.below_floor + report.untagged} of these carry a topic tag "
            "that has not been verified. They will be listed under that topic for "
            "students. Run `tag-verify-structured` on them.[/yellow]"
        )
    if dry_run:
        console.print(
            "[yellow]dry run: nothing was written. Re-run with --no-dry-run to approve.[/yellow]"
        )


@app.command()
def embed(
    subject: str = typer.Option("physics-5054", help="Subject slug to embed."),
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    dry_run: bool = typer.Option(False, help="Compute and report, write nothing."),
) -> None:
    """Embed every approved question in a subject for the retrieval index.

    Only approved questions are embedded — `match_question` never looks at
    anything else, so a question still in the review queue would be a cost
    with no possible payoff. Verbatim-duplicate MCQs across paper variants
    (`canonical_question_id`, from `dedupe`) share one vector rather than
    paying once per paper, and content is hashed so re-running this after a
    fresh backfill only pays for questions whose text is new or changed.
    """
    from .embed import backfill_embeddings
    from .load import connect

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)
    if not settings.voyage_api_key:
        console.print("[red]VOYAGE_API_KEY is not set.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        report = backfill_embeddings(conn, subject, papers, dry_run=dry_run)
        if dry_run:
            conn.rollback()
            console.print("[yellow]dry run: rolled back[/yellow]")
        else:
            conn.commit()

    table = Table("", "", title=f"{subject} — embeddings")
    table.add_row("approved questions considered", str(report.candidates))
    table.add_row("duplicate groups", str(report.groups))
    table.add_row("Voyage API calls", str(report.api_calls))
    table.add_row("reused from a duplicate (no API call)", str(report.reused_from_duplicate))
    table.add_row("already up to date, skipped", str(report.unchanged))
    table.add_row("rows written", str(report.written))
    console.print(table)

    if report.missing_text:
        console.print(
            f"[yellow]{len(report.missing_text)} question(s) had no text to embed "
            f"(no source PDF on disk, or an all-figure MCQ): "
            f"{report.missing_text[:10]}[/yellow]"
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


@app.command(name="fix-crops")
def fix_crops(
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    dpi: int = typer.Option(
        150, help="Crop resolution — matches load-mcq/load-structured's default."
    ),
    dry_run: bool = typer.Option(False, help="Audit and re-crop, but do not upload."),
) -> None:
    """Re-render and re-upload any live or reviewable question's crop the bucket is missing.

    question_assets.bbox exists precisely so this is possible without
    re-running extraction: the database already knows where the crop is on
    the page, so a missing file is a re-render, not a re-ingestion. Covers
    approved, needs_review and extracted questions — not rejected, which
    nobody is ever shown again. Audits against the bucket first, then fixes
    only what is actually gone.
    """
    from .crops import find_missing_crops, fix_missing_crops
    from .load import connect
    from .storage import SupabaseStorage

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    storage = SupabaseStorage.from_settings(settings)
    if storage is None:
        console.print("[red]Supabase Storage is not configured.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        with console.status("auditing crops against storage..."):
            missing, checked_folders = find_missing_crops(conn, storage)

    console.print(f"checked {checked_folders} paper folders")
    if not missing:
        console.print("[green]Nothing missing.[/green]")
        return

    by_subject: dict[str, int] = {}
    for item in missing:
        by_subject[item.subject_slug] = by_subject.get(item.subject_slug, 0) + 1
    console.print(f"[yellow]{len(missing)} crop(s) missing from storage:[/yellow] {by_subject}")

    with console.status(f"re-rendering{' (dry run)' if dry_run else ''}..."):
        report = fix_missing_crops(
            missing,
            papers_dir=papers,
            storage=storage,
            work_dir=settings.work_dir,
            dpi=dpi,
            dry_run=dry_run,
        )

    table = Table("", "", title="Crop recovery" + (" (dry run)" if dry_run else ""))
    table.add_row("found missing", str(report.found_missing))
    table.add_row("fixed", str(report.fixed))
    table.add_row("no source PDF", str(len(report.no_source_pdf)))
    table.add_row("crop failed", str(len(report.crop_failed)))
    table.add_row("upload failed", str(len(report.upload_failed)))
    console.print(table)

    for label, keys in [
        ("no source PDF", report.no_source_pdf),
        ("crop failed", report.crop_failed),
        ("upload failed", report.upload_failed),
    ]:
        if keys:
            console.print(f"[red]{label}:[/red] {keys[:10]}" + (" ..." if len(keys) > 10 else ""))


@app.command(name="upload-papers")
def upload_papers(
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    dry_run: bool = typer.Option(False, help="Audit only, upload nothing."),
) -> None:
    """Upload a paper's own PDFs (question paper, mark scheme, ...) to the
    bucket, for every `paper_documents` row that names a file the bucket does
    not actually have yet.

    Ingestion has recorded `paper_documents.storage_key` since the very
    first backfill — the metadata (page count, byte size, checksum) has
    always been right. Only the file itself was ever missing, which is why
    the split viewer's panes have stayed a placeholder. Safe to re-run at
    any time: a paper the bucket already has costs one list call and
    nothing else.
    """
    from .load import connect
    from .paper_documents import find_missing_documents, upload_missing_documents
    from .storage import SupabaseStorage

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    storage = SupabaseStorage.from_settings(settings)
    if storage is None:
        console.print("[red]Supabase Storage is not configured.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        with console.status("auditing documents against storage..."):
            missing, checked_folders = find_missing_documents(conn, storage)

    console.print(f"checked {checked_folders} paper folder(s)")
    if not missing:
        console.print("[green]Nothing missing.[/green]")
        return

    by_subject: dict[str, int] = {}
    for item in missing:
        by_subject[item.subject_slug] = by_subject.get(item.subject_slug, 0) + 1
    console.print(f"[yellow]{len(missing)} document(s) missing from storage:[/yellow] {by_subject}")

    with console.status(f"uploading{' (dry run)' if dry_run else ''}..."):
        report = upload_missing_documents(
            missing, papers_dir=papers, storage=storage, dry_run=dry_run
        )

    table = Table("", "", title="Document upload" + (" (dry run)" if dry_run else ""))
    table.add_row("found missing", str(report.found_missing))
    table.add_row("uploaded", str(report.uploaded))
    table.add_row("no source PDF", str(len(report.no_source_pdf)))
    table.add_row("upload failed", str(len(report.upload_failed)))
    console.print(table)

    for label, keys in [
        ("no source PDF", report.no_source_pdf),
        ("upload failed", report.upload_failed),
    ]:
        if keys:
            console.print(f"[red]{label}:[/red] {keys[:10]}" + (" ..." if len(keys) > 10 else ""))


@app.command(name="load-grade-thresholds")
def load_grade_thresholds_cmd(
    papers: Path = typer.Option(Path("papers"), help="Where the downloaded PDFs live."),
    dry_run: bool = typer.Option(False, help="Parse and report, write nothing."),
) -> None:
    """Parse every local `{code}_{season}{yy}_gt.pdf` and load its thresholds.

    A grade-threshold PDF publishes two tables (see `grade_thresholds.py` for
    why they are two separate database tables): thresholds per component,
    and thresholds per combination of components -- the one that actually
    determines a candidate's overall grade. The PDF itself is also
    registered as a `paper_documents` row against every paper in its session
    (one file covers every component), so `upload-papers` picks it up the
    same way it picks up every question paper and mark scheme.

    Fetch the source files first with `scripts/fetch_grade_thresholds.py`,
    which downloads them from Cambridge's own public site.
    """
    from .grade_thresholds import parse_grade_threshold_pdf
    from .ingest import file_digest, page_count
    from .load import (
        connect,
        find_exam_session_id,
        find_subject_id,
        record_component_grade_thresholds,
        record_document,
        record_grade_thresholds,
    )
    from .naming import document_key, parse_paper_filename, storage_prefix

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    gt_files = sorted(papers.glob("*/*_gt.pdf"))
    if not gt_files:
        console.print("[yellow]No local *_gt.pdf files found.[/yellow]")
        return

    loaded = skipped = failed = 0
    with connect(settings.database_url) as conn:
        for path in gt_files:
            try:
                parsed = parse_paper_filename(path.stem)
            except ValueError as error:
                console.print(f"[red]{path.name}: {error}[/red]")
                failed += 1
                continue

            subject_id = find_subject_id(conn, parsed.syllabus_code)
            session_id = find_exam_session_id(conn, parsed.year, parsed.season)
            if not subject_id or not session_id:
                console.print(f"[yellow]{path.name}: unknown subject or session[/yellow]")
                skipped += 1
                continue

            try:
                components, combinations = parse_grade_threshold_pdf(path)
            except ValueError as error:
                console.print(f"[red]{path.name}: {error}[/red]")
                failed += 1
                continue

            if dry_run:
                console.print(
                    f"{path.name}: {len(components)} component(s), "
                    f"{len(combinations)} combination(s)"
                )
                loaded += 1
                continue

            for comp in components:
                record_component_grade_thresholds(
                    conn, subject_id, session_id, comp.component, comp.variant,
                    [(r.grade, r.min_mark, r.max_mark) for r in comp.rows],
                )
            for combo in combinations:
                record_grade_thresholds(
                    conn, subject_id, session_id,
                    combination=combo.combination, components=combo.components,
                    rows=[(r.grade, r.min_mark, r.max_mark) for r in combo.rows],
                )

            with conn.cursor() as cur:
                cur.execute(
                    "select id, component, variant from papers "
                    "where subject_id = %s and exam_session_id = %s",
                    (subject_id, session_id),
                )
                paper_rows = cur.fetchall()
            for paper_row in paper_rows:
                prefix = storage_prefix(
                    parsed.syllabus_code, parsed.year, parsed.season,
                    paper_row["component"], paper_row["variant"],
                )
                record_document(
                    conn, paper_row["id"], doc_type="gt",
                    storage_key=document_key(prefix, "gt"),
                    page_count=page_count(path),
                    byte_size=path.stat().st_size,
                    checksum=file_digest(path),
                )
            conn.commit()
            loaded += 1

    table = Table("", "", title="Grade thresholds" + (" (dry run)" if dry_run else ""))
    table.add_row("PDFs found", str(len(gt_files)))
    table.add_row("loaded", str(loaded))
    table.add_row("skipped (unknown subject/session)", str(skipped))
    table.add_row("failed to parse", str(failed))
    console.print(table)


@app.command(name="fix-spurious-crops")
def fix_spurious_crops(
    min_height_pt: float = typer.Option(
        30.0, help="Below this, a trailing page-crop is treated as a page header, not content."
    ),
    dry_run: bool = typer.Option(False, help="Report what would be removed, but remove nothing."),
) -> None:
    """Remove a structured question's trailing crop when it shows the next
    question instead of anything of its own.

    The segmenter sometimes marks a question as continuing onto a page it
    does not actually reach, and grabs that page's header/barcode strip as
    the "continuation" — a bbox a handful of points tall, the same height
    regardless of subject or paper, which is what makes this detectable at
    all. There is no correct crop to render here, only a row that should not
    exist: the content it points at belongs to a different question.
    """
    from .load import connect
    from .segmentation_fixes import find_spurious_continuation_crops, remove_spurious_crops
    from .storage import SupabaseStorage

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    storage = SupabaseStorage.from_settings(settings)
    if storage is None:
        console.print("[red]Supabase Storage is not configured.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        with console.status("auditing structured questions..."):
            crops = find_spurious_continuation_crops(conn, min_height_pt=min_height_pt)

        if not crops:
            console.print("[green]Nothing found.[/green]")
            return

        by_subject: dict[str, int] = {}
        for c in crops:
            subject = c.paper_slug.rsplit("-", 4)[0]
            by_subject[subject] = by_subject.get(subject, 0) + 1
        console.print(
            f"[yellow]{len(crops)} spurious trailing crop(s) found:[/yellow] {by_subject}"
        )
        for c in crops[:10]:
            console.print(
                f"  {c.paper_slug} Q{c.display_label} page {c.page_number} ({c.height_pt:.1f}pt)"
            )
        if len(crops) > 10:
            console.print(f"  ... and {len(crops) - 10} more")

        removed = remove_spurious_crops(conn, storage, crops, dry_run=dry_run)
        if dry_run:
            conn.rollback()
            console.print(f"[yellow]dry run: would remove {removed}, rolled back[/yellow]")
        else:
            conn.commit()
            console.print(f"[green]removed {removed}[/green]")


@app.command(name="clean-control-characters")
def clean_control_characters_cmd(
    dry_run: bool = typer.Option(
        True, help="Report what would change, but write nothing. Pass --no-dry-run to apply."
    ),
) -> None:
    """Remove barcode and symbol-font control characters from stored question
    text and mark schemes.

    Ingest now cleans this as it loads (`text_clean.py`); this brings the rows
    loaded before that up to the same state. Idempotent: a row with nothing
    left to clean is not touched, so running it twice changes nothing.
    """
    from .load import connect
    from .text_clean import clean_extracted_text

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    # (column, is it a mark scheme -- where the glyph is a symbol to keep)
    columns = (("question_text", False), ("mark_scheme_text", True), ("examiner_comment", False))
    control = "[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]"

    with connect(settings.database_url) as conn:
        changed = 0
        for column, symbols in columns:
            with conn.cursor() as cur:
                cur.execute(
                    f"select id, {column} as text from questions where {column} ~ %s",
                    (control,),
                )
                rows = cur.fetchall()
                for row in rows:
                    cleaned = clean_extracted_text(row["text"], symbols=symbols)
                    cur.execute(
                        f"update questions set {column} = %s where id = %s",
                        (cleaned or None, row["id"]),
                    )
                changed += len(rows)
            console.print(f"{column}: {len(rows)} row(s) with control characters")

        if dry_run:
            conn.rollback()
            console.print(f"[yellow]dry run: would clean {changed} value(s), rolled back[/yellow]")
        else:
            conn.commit()
            console.print(f"[green]cleaned {changed} value(s)[/green]")


@app.command(name="audit-mcq-crops")
def audit_mcq_crops_cmd(
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    subject: list[str] = typer.Option(
        None, "--subject",
        help="Restrict to this subject slug. Repeatable; default is every subject.",
    ),
) -> None:
    """Re-derive every approved or pending MCQ's crop region from its source
    PDF, and report any whose stored bbox hides an option the recomputed one
    reveals.

    Read-only: this only reports. Eight approved, published crops shipping
    without their fourth option — one of them the correct answer — is what
    this exists to catch before it happens again, not to fix silently; a
    silent auto-fix here is the same shape of risk bulk approval already is.
    Exits 1 if anything was found, so this can gate a script without a
    person having to read the table first.
    """
    from .crop_audit import audit_mcq_crops
    from .load import connect

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        with console.status("re-segmenting source PDFs..."):
            report = audit_mcq_crops(conn, papers, subject_slugs=subject or None)

    console.print(f"checked {report.checked} question(s)")
    if report.skipped_missing_pdf:
        console.print(
            f"[yellow]{len(report.skipped_missing_pdf)} paper(s) had no local PDF to "
            "check against[/yellow]"
        )

    if not report.suspects:
        console.print("[green]Nothing found.[/green]")
        return

    table = Table("subject", "paper", "Q", "answer", "kind", "detail")
    for s in report.suspects:
        table.add_row(
            s.subject_slug, s.paper_slug, s.display_label, s.correct_option or "?",
            s.kind, s.detail,
        )
    console.print(table)
    console.print(f"[red]{len(report.suspects)} suspect crop(s) found.[/red]")
    raise typer.Exit(code=1)


@app.command(name="marks-export")
def marks_export(
    papers: Path = typer.Option(Path("papers"), help="Where the source PDFs live."),
    out: Path = typer.Option(None, help="Where to write the worksheet JSON."),
    limit: int = typer.Option(None, help="Stop after this many parent questions."),
) -> None:
    """Export structured leaves whose max_marks never landed, with their
    parent's crop re-rendered locally to read.

    No model, no API key: the crop comes from the source PDF via the bbox
    already recorded, exactly like `fix-crops`. Read each parent's crop(s),
    find the mark bracket for each listed leaf, and feed the answers back
    with `marks-apply`.
    """
    from .load import connect
    from .marks_recovery import build_marks_worksheet, write_worksheet

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    with connect(settings.database_url) as conn:
        with console.status("rendering crops..."):
            parents = build_marks_worksheet(
                conn, papers_dir=papers, work_dir=settings.work_dir, limit=limit
            )

    out = out or settings.work_dir / "marks-worksheet.json"
    write_worksheet(parents, out)

    total_leaves = sum(len(p.leaves) for p in parents)
    no_crops = sum(1 for p in parents if not p.crop_paths)
    table = Table("", "", title="Marks worksheet")
    table.add_row("parent questions", str(len(parents)))
    table.add_row("leaves to read", str(total_leaves))
    if no_crops:
        table.add_row(
            "[red]no crop rendered[/red]",
            f"{no_crops} — see log: no source PDF, or no crops at all",
        )
    console.print(table)
    console.print(f"-> {out}")


@app.command(name="marks-apply")
def marks_apply(
    decisions: Path = typer.Argument(..., exists=True, help="JSON list of {leaf_id, max_marks}."),
    dry_run: bool = typer.Option(False, help="Do the work, then roll it back."),
) -> None:
    """Write recovered max_marks values back to their leaves."""
    import json

    from .load import connect
    from .marks_recovery import apply_marks_decisions

    if not settings.database_url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=2)

    payload = json.loads(decisions.read_text(encoding="utf-8"))
    entries = payload["decisions"] if isinstance(payload, dict) else payload

    with connect(settings.database_url) as conn:
        report = apply_marks_decisions(conn, entries)
        if dry_run:
            conn.rollback()
            console.print("[yellow]dry run: rolled back[/yellow]")
        else:
            conn.commit()

    table = Table("", "", title="Marks written")
    table.add_row("updated", str(report.updated))
    if report.unknown_leaf_ids:
        table.add_row(
            "[red]unknown or already-set leaf ids[/red]", str(len(report.unknown_leaf_ids))
        )
    if report.invalid_marks:
        table.add_row("[red]invalid marks value[/red]", str(len(report.invalid_marks)))
    console.print(table)
    if report.unknown_leaf_ids:
        console.print(f"[red]{report.unknown_leaf_ids[:10]}[/red]")


if __name__ == "__main__":
    app()

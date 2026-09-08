# Ingestion pipeline

Turns past-paper PDFs into a segmented, mark-scheme-linked, topic-tagged question
bank. This is where the product actually lives — every competitor has the same
PDFs; almost none of them have this correctly.

Deliberately a batch CLI, not a service. Ingestion has no uptime requirement, no
request path and nobody waiting on it, so a FastAPI app would only add a
deployment target that earns nothing. It runs, it writes to Postgres, it exits.

## Setup

```bash
cd pipeline
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
export ANTHROPIC_API_KEY=... VOYAGE_API_KEY=... DATABASE_URL=...
```

## Stages

```
PDF ──▶ render ──▶ extract ──▶ cross-check ──▶ crop ──┐
                                                      ├──▶ match ──▶ tag ──▶ embed ──▶ load
mark scheme PDF ──▶ render ──▶ extract entries ───────┘
```

| Stage | Module | What it does |
|---|---|---|
| render | `render.py` | Rasterise pages at 200 dpi, pull the embedded text layer |
| segment (MCQ) | `segment.py` | Locate questions geometrically — no model, no API |
| extract | `extract.py` | Vision model returns questions in a fixed schema |
| cross-check | `extract.py` | Compare against the text layer; flag disagreements |
| crop | `render.py` | Cut each question's image region out of the source page |
| match | `markscheme.py` | Pair questions with mark-scheme entries by exact label |
| tag | `tagging.py` | Assign syllabus topics from a closed list, with confidence |
| embed | `embed.py` | Vectors for topical search and question identification |
| load | `load.py` | Idempotent writes, as `extracted` / `needs_review` |

```bash
noteacademy render      paper.pdf --out work/5054-2026-mj-11
noteacademy segment-mcq qp.pdf --out work/crops     # geometric, free
noteacademy mcq-key     ms.pdf                      # text layer, free
noteacademy extract     work/5054-2026-mj-11 --pdf qp.pdf
noteacademy estimate    --papers 3400 --pages 14
```

## Measured against a real paper

Verified against three real papers spanning eleven years — **5054/11 May/June
2015, 2019 and 2026** — question papers and mark schemes, not assumed:

| | 2015 | 2019 | 2026 |
|---|---|---|---|
| Text layer present | yes | yes | yes |
| Questions segmented | **40/40** | **40/40** | **40/40** |
| Answer key parsed | **40/40** | **40/40** | **40/40** |
| Mark scheme layout | legacy | modern | modern |

**The question-paper template is stable across all eleven years.** The
question-number gutter sits at x = 49.6pt in every paper examined, and every page
is A4. Segmentation needed no adjustment for the older papers.

**The mark scheme layout is not.** Papers up to ~2015 use a "Question Number /
Key" table in *two side-by-side columns* with no marks column, so reading order
interleaves them (1 B 21 D / 2 A 22 C). From ~2019 it is a single
"Question / Answer / Marks" table. Both are parsed, selected on the header —
deliberately as two strict parsers rather than one permissive one, because a
loose number-then-letter rule would also read a syllabus code or page number as
an answer.

This is exactly why the corpus is validated across years before a backfill: the
2015 mark scheme returned **zero** answers against the original parser, silently,
and would have left an eleven-year hole in the bank.

**Multiple-choice papers need no inference at all.** The mark scheme is a
Question/Answer/Marks table that survives extraction intact, and CAIE lays
questions out on a strict grid with the number alone in a left gutter — so both
the answer key and the question boundaries are facts about the page rather than
things a model has to infer. That removes the cost *and* the hallucination risk
from the highest-value part of the corpus. Vision remains the fallback for
scanned or non-conforming papers, and the route for structured papers.

**Text extraction alone would produce a broken product.** In the real paper:

- Reading order is scrambled. Question numbers come *after* their stem, and
  option letters *after* their option text ("The cyclist is at rest." then "A").
- Options frequently extract in reverse — D, C, B, A.
- All 40 diagrams are **vector artwork, not images**: 12 of 16 pages carry
  substantial vector drawings and the file contains zero embedded rasters. A
  speed–time graph extracts as a scatter of axis labels — "10 5 0 0 2 4 6 8 10
  12 time / s" — which is unanswerable.

This is why crops are the display artifact and text is only the search index.
The rendered crop shows the question exactly as printed, options in the right
order, graph intact.

## Six decisions that matter more than the code

**Crops are the display artifact; text is only the search index.** Diagrams,
graphs, circuit symbols and structural formulae do not survive text extraction —
which is most of what makes a physics or chemistry question answerable. So every
question stores both a rendered crop and its bounding box, and students see the
crop. Keeping the bbox means crops can be re-rendered at any resolution later
without re-running extraction.

**A vision model, not a layout stack.** CAIE layouts vary across 25 years and 40
subjects; a LayoutLM/Detectron pipeline needs retuning per variation, and a
vision model reads all of them. Structured output means it cannot return a shape
the loader fails to parse.

**The text layer is ground truth.** CAIE PDFs are digitally produced and nearly
always carry real text. `cross_check` compares the model's question labels and
bounding boxes against it, which is what catches a hallucinated question before
it reaches the database. Scanned papers (rare, mostly pre-2005) skip the check
rather than generate phantom warnings that train reviewers to ignore the queue.

**Topic tagging picks from a closed list.** The syllabus's own learning outcomes
are supplied as an enum; the model never invents a code, and the loader drops
anything not on the list. Every assignment carries a confidence, and anything
under the floor goes to a human instead of to students. Tagging accuracy *is* the
product — a topical bank that is 80% right is worse than none, because a student
revising Kinematics gets a Thermal Physics question and stops trusting the site.

**Matching is exact, never fuzzy.** Fuzzy fallback raises the match rate and
lowers the accuracy, which is the wrong trade for a revision tool. Anything that
does not match exactly is reported, not guessed at. When two mark-scheme entries
claim one question, *both* are withdrawn — keeping the first would just be
picking one at random.

**Do MCQ papers first.** An MCQ mark scheme is an answer grid, so `mcq-key`
produces a trustworthy answer key with no segmentation problem at all. That is
enough to run the whole practice arena and start generating attempt data, months
before structured-paper segmentation is reliable. It is the cheapest useful thing
here by a wide margin.

## Cost

Multiple-choice papers now cost **nothing** — `mcq-key` and `segment-mcq` are
deterministic. That covers Paper 1 across every subject, which is the wedge.

`noteacademy estimate` prices the remaining structured papers. Roughly ten
subjects across sixteen years — ~3,400 documents, ~48k pages — came to about
$1,400 at `claude-opus-5` list pricing before the MCQ path was made free;
excluding Paper 1 and its mark schemes takes a meaningful bite out of that.
Re-measure once a structured paper has actually been through the vision path.

Inference is not the constraint. **Human review is.** Budget for the review
queue, not for tokens, and do not economise on the extraction model: a
segmentation or tagging error is written once and then silently degrades every
feature built on top of it.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check noteacademy_pipeline tests
```

Covers the deterministic half — label normalisation, mark-scheme matching, and
the cross-check that keeps hallucinated questions out of the database. The model
calls themselves are not mocked; they are exercised against real papers.

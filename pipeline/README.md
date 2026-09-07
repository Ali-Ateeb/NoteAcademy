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
| extract | `extract.py` | Vision model returns questions in a fixed schema |
| cross-check | `extract.py` | Compare against the text layer; flag disagreements |
| crop | `render.py` | Cut each question's image region out of the source page |
| match | `markscheme.py` | Pair questions with mark-scheme entries by exact label |
| tag | `tagging.py` | Assign syllabus topics from a closed list, with confidence |
| embed | `embed.py` | Vectors for topical search and question identification |
| load | `load.py` | Idempotent writes, as `extracted` / `needs_review` |

```bash
noteacademy render  paper.pdf --out work/5054-2019-mj-12
noteacademy extract work/5054-2019-mj-12 --pdf paper.pdf
noteacademy mcq-key ms.pdf
noteacademy estimate --papers 3400 --pages 14
```

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

`noteacademy estimate` prices a backfill before you commit to one. Roughly ten
subjects across sixteen years — ~3,400 documents, ~48k pages — comes to about
$1,400 of extraction at `claude-opus-5` list pricing, assuming the cached system
prefix. Measure on one real subject and re-run with the numbers you observed.

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

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
```

Configuration comes from a `.env` at the repository root (copy `.env.example`),
found by walking up from wherever the CLI is run. Real environment variables
always override it, so CI and production set variables directly.

**Multiple-choice papers need no keys at all** — `segment-mcq` and `mcq-key` are
deterministic. `ANTHROPIC_API_KEY` is required only for structured papers, topic
tagging and the vision fallback; `VOYAGE_API_KEY` only for the retrieval index;
`DATABASE_URL` only to load results.

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
| ingest (MCQ) | `ingest.py` | The three deterministic stages, straight into Postgres |
| syllabus | `syllabus.py` | Read the published syllabus PDF into the topic tree |
| upload | `storage.py` | Put the crops where the app can serve them from |
| verify | `verify.py` | A second, independent topic tag, checked against the first |

```bash
noteacademy render      paper.pdf --out work/5054-2026-mj-11
noteacademy segment-mcq qp.pdf --out work/crops     # geometric, free
noteacademy mcq-key     ms.pdf                      # text layer, free
noteacademy load-mcq    5054_s19_qp_11.pdf 5054_s19_ms_11.pdf --crops work/crops
                        # crops upload automatically when storage is configured
noteacademy load-syllabus 5054-2026-2028-syllabus.pdf --source-url https://...
noteacademy extract     work/5054-2026-mj-11 --pdf qp.pdf
noteacademy estimate    --papers 3400 --pages 14
```

`load-syllabus` reads the topic tree out of the published syllabus PDF. The tree
is what tagging picks from, what the topical browser renders, and what carries a
student across a syllabus revision, so transcribing it by hand is both a day per
subject and the place the least visible errors get in: a missing outcome cannot
be seen, and a mistyped code silently detaches every question tagged with it.

CAIE typesets these from one template, and the parse reads the template's
geometry rather than guessing from text: 13pt bold is a section, 10pt bold a
topic, 10pt regular in the number gutter a sub-topic, and everything at x=85 the
outcome beside it.

The part worth knowing about is the equations. They are set as stacked
fractions, so `speed = distance / time` extracts as two lines and joins into
`speed = distance time` — not a worse rendering of the equation but a false one.
The fraction *bar* is drawn on the page, so the parser pairs the lines above and
below each bar and joins them with a slash, and reports any bar it could not
resolve rather than flattening it. Measured on Physics 5054 (2026-2028): 6
sections, 83 topics, 270 learning outcomes, 41 of 41 fraction bars resolved.

`load-mcq` is the whole multiple-choice path in one command: segment, read the
answer key, crop, and write the paper, its documents and its questions to the
database. No model, no API key, and idempotent — re-running a paper updates it
rather than duplicating it.

Both filenames are Cambridge's own (`5054_s19_qp_11.pdf`), so the session,
component and variant are read from them rather than retyped as flags, and the
two files are checked against each other before anything is written. A mark
scheme from the right session and the wrong variant is the ingestion mistake that
matters most: every answer it supplies is a plausible letter and most of them are
wrong, and nothing downstream could tell.

Crops upload as part of the same run when `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY` are set — Supabase Storage needs no second account
and no S3 keys, and the bucket stays private with the app handing out
short-lived signed URLs. `--no-upload` keeps them local. The storage *key* is
what the database records, never a URL, so swapping the bucket for R2 or S3
later changes this module and nothing else.

What it deliberately does *not* write is question and option text. Reading order
in these papers is scrambled (see below), so text taken from them would look
correct in the database and be wrong on the screen. A question arrives as its
number, its answer and its crop; text comes later, from the vision pass, checked
against the page. Everything lands unapproved.

## Measured against real papers

Twenty-eight question papers and their mark schemes, across **three subjects**
(Physics 5054, Chemistry 5070, Biology 5090) and **eleven years** (2010-2026):
**40/40 questions segmented and 40/40 answers parsed on every one.** The
question-number gutter sits at x = 49.6pt in all of them, so the geometry holds
across subjects as well as across years.

One of the twenty-eight did not, at first — and it is the more useful result.
Chemistry 5070/12 from October/November 2019 heads its answer column "Mark"
where 5070/11 from the same session heads it "Marks". One character, and the
strict parser refused the whole paper: forty questions, no answers, and nothing
a reviewer could do but retype the key. The pattern now accepts either spelling.
The lesson is the one this pipeline keeps relearning — the parser refusing was
correct, and the only way to find out it was refusing was to run it over a wide
enough spread of real papers.

Detail on the three papers used to develop the segmenter — **5054/11 May/June
2015, 2019 and 2026**:

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

## Verifying a tagging pass without a review queue of 600

The first tagging pass ran against 600 Physics 5054 questions and produced a
number nobody had checked: every tag came from one model reading the question's
extracted *text*, and text extraction is the part of this pipeline already known
to scramble reading order and lose diagrams entirely. Reviewing all 600 by hand
does not scale, and most of the rest of the queue does not need it either — the
answer keys are parsed from a machine-readable table and validate 40/40 on every
paper, and the crops are geometrically segmented with nothing shaped like an
outlier. The one thing worth checking is the tag.

So a second, independent pass tagged the same 600 questions from their *crops*
— the question as printed, the same thing a reviewer would look at — with the
first pass's answer withheld, and the two were compared:

| | |
|---|---|
| Agreed | **564 / 600 (94%)** |
| Disagreed, sent to review | 36 (6%) |
| Already approved, left untouched | 2 |

Agreement raised the tag's confidence (`max(pass1, 0.9)`); disagreement recorded
the second opinion as a secondary topic and dropped the primary below the review
floor — the same mechanism the first pass already uses for "unsure", because two
independent methods landing on different topics *is* unsure. Nothing new was
invented to represent it.

The 36 disagreements are not noise. Almost every one was already a low-confidence
tag from the first pass (0.60–0.74) — the pattern the first pass's own confidence
score was supposed to predict, borne out by a second, independent reading. And
they cluster where the syllabus itself is genuinely ambiguous: motion questions
that are really about forces (`1.2` vs `1.5.1`/`1.5.2`), practical-electricity
questions that are really about power (`4.4.1` vs `4.2.2`/`4.2.3`), circuit
diagrams versus circuit *behaviour* (`4.3.1` vs `4.3.2`/`4.3.3`). These are
exactly the questions a human should be looking at, and now they sort to the top
of the queue instead of being buried in 600.

```bash
noteacademy tag-verify-export --subject physics-5054   # sheets + manifest
# look at the sheets, decide a topic per position, write decisions.json
noteacademy tag-verify-apply decisions.json
```

`tag-verify-export` builds JPEG contact sheets of every already-tagged
question's crop — duplicate questions across paper variants folded to one copy,
since CAIE's component 11/12 pairs share most of their multiple-choice items
verbatim — and a manifest mapping each sheet position back to the question(s) it
stands for. The manifest never carries the first pass's tag; that withholding is
the entire point of a second pass, not an implementation detail. `decisions.json`
is `{"S3.7": "4.2.4", ...}`, one topic code per position, from whoever looks at
the sheets — a person, or a Claude Code session with no API key, the same as the
first pass.

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

Covers the deterministic half — label normalisation, mark-scheme matching,
filename parsing, the question-paper/mark-scheme agreement check, and the
cross-check that keeps hallucinated questions out of the database. The model
calls themselves are not mocked; they are exercised against real papers.

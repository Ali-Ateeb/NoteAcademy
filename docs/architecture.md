# Architecture

## Why the stack is smaller than the obvious one

A first sketch of this system usually lands on Next.js + FastAPI + Redis +
Pinecone + S3 + Stripe. That is six deploy surfaces before there is a single
user. This repo collapses it to two:

| Obvious choice | Here | Why |
|---|---|---|
| Next.js + a FastAPI backend | Next.js full-stack | One deploy. Route handlers and server components cover the API. |
| FastAPI as a live service | Python as offline batch jobs | Ingestion has no request path and no uptime requirement. Making it a service adds a deploy target that earns nothing. |
| Pinecone / Qdrant | pgvector | The query that matters is hybrid — *questions similar to this, restricted to this subject, this syllabus version, approved only*. With vectors beside that metadata it is one indexed query; with a separate vector store it is a fan-out plus a join in application code. |
| Redis | nothing, yet | Postgres handles the first 10k users. Add Upstash when a specific query hurts, not before. |
| Stripe + a local gateway | manual verification first | See Payments below. |

Python earns its place for exactly one thing: PDF handling and vision
extraction. It is not in the request path.

## Data model

Three decisions that are hard to retrofit:

**Topics belong to a syllabus version, not a subject.** CAIE revises syllabuses,
so "Kinematics" in the 2020–2022 Physics 5054 tree is not necessarily the same
node as in 2023–2025, and a 2014 question may test an outcome that has since been
withdrawn. `topic_links` maps nodes across revisions with an explicit relation
(`same`, `split_into`, `merged_into`, `withdrawn`). Without this the topical
engine silently rots and, in a few years, serves students content that is no
longer on their syllabus. Getting it right is a selling point: *filtered to your
syllabus*.

**A paper is the sitting; its documents hang off it.** Putting `doc_type` on
`papers` — the common shortcut — makes a question paper and its mark scheme
unrelated rows with nothing to join on. That join is exactly what the
split-screen viewer needs. Grade thresholds are per-session and examiner reports
often cover all variants of a component, which the split handles naturally.

**`attempts` is append-only, ordered by `seq`.** A changed answer is a new row;
"their answer" is the highest `seq`. Ordering by `created_at` does not work:
`now()` is the transaction timestamp, so rows written together share it exactly
and the ordering between them is undefined. Every analytic is a view over this
table, so any metric can be recomputed from history rather than migrated.

Supporting these: `question_topics` is many-to-many with a confidence and a
source, because real questions straddle topics and because the confidence column
is what drives the human review queue. Questions self-reference through
`parent_question_id`, so 1(a)(ii) rolls up into 1(a) into 1.

## Ingestion

```
PDF ─▶ render ─▶ vision extract ─▶ cross-check ─▶ crop ─┐
                                                        ├─▶ match ─▶ tag ─▶ embed ─▶ load
mark scheme ─▶ render ─▶ extract entries ───────────────┘
```

A vision model rather than a layout-analysis stack: CAIE layouts vary across 25
years and 40 subjects, and a classical pipeline needs retuning per variation.
Structured output means the model cannot return a shape the loader fails to
parse.

The PDF text layer is ground truth. CAIE papers are digitally produced and
almost always carry real text, so `cross_check` compares the model's question
labels and bounding boxes against it — which is what catches a hallucinated
question before it reaches the database.

**Crops are the display artifact; text is only the search index.** Diagrams,
graphs, circuit symbols and structural formulae do not survive text extraction,
and that is most of what makes a physics or chemistry question answerable. Each
question stores a rendered crop *and* its bounding box, so crops can be
re-rendered at any resolution later without re-running extraction. This is the
detail most builds get wrong.

Cost: roughly $1,400 for a ten-subject, sixteen-year backfill at `claude-opus-5`
list pricing (`noteacademy estimate`). Inference is not the constraint — human
review is. Budget for the review queue.

## The AI solver

The design that hallucinates less: **for a question already in the bank, do not
do RAG at all.** Embed the student's pasted question, match it against the
question index, and above the similarity threshold you have *identified* the
question — inject its verbatim mark scheme and examiner report. No retrieval
fuzziness, no synthesis over chunks. That is what `match_question()` in migration
0007 is for.

RAG over mark-scheme and examiner-report chunks is the fallback for questions
that are not in the bank. And a hard rule: if nothing is retrieved above
threshold, say so and label any explanation as unofficial. The moment a student
catches the assistant contradicting a real mark scheme, the differentiation is
gone.

## Payments

The core market is Pakistan, India, Bangladesh and the Gulf. Stripe does not
reach most of it and card ownership among sixteen-year-olds is close to zero.
`payment_submissions` is a manual verification queue: wallet or bank transfer,
receipt screenshot, admin approval. It ships in a day instead of six weeks and
proves willingness to pay before anyone integrates a gateway. When a real gateway
lands it becomes just another provider writing the same rows.

Price low and local, and note that demand is seasonal — students buy in the
run-up to May/June.

## What "premium" actually means here

Not gradients. A viewer that opens instantly, synchronised scroll between the
question paper and the mark scheme, keyboard navigation everywhere, no layout
shift, and a correct focus ring. Speed reads as quality. The design system is
committed to in week one precisely so it is not retrofitted across forty screens
later.

/** Shapes the UI consumes. They mirror the database schema in db/migrations,
 *  narrowed to what a page actually renders. */

export type LevelCode = "o_level" | "igcse" | "as_level" | "a_level";
export type Season = "feb_march" | "may_june" | "oct_nov";
export type QuestionType = "mcq" | "structured" | "essay" | "practical";
export type McqOption = "A" | "B" | "C" | "D";
export type PaperDocType = "qp" | "ms" | "er" | "gt" | "in";

export interface Level {
  code: LevelCode;
  name: string;
  slug: string;
}

export interface Subject {
  slug: string;
  levelCode: LevelCode;
  syllabusCode: string;
  title: string;
  description: string;
  /** Subjects still being ingested are listed but not linked. Shipping one
   *  subject done properly beats forty at 70% tagging accuracy. */
  isPublished: boolean;
}

export interface Topic {
  code: string;
  slug: string;
  title: string;
  learningObjectives: string[];
  questionCount: number;
  /** '4.2.4' hangs off '4.2' hangs off '4'. Null for a top-level section.
   *  Enough to rebuild the tree without a recursive query. */
  parentCode: string | null;
  /** Does this node carry learning outcomes of its own?
   *
   *  CAIE trees mix two kinds of node: '4.2 Electrical quantities' exists to
   *  group '4.2.1'–'4.2.4' and has no outcomes, while '1.2 Motion' has
   *  thirteen. Only the second kind is something a student revises or a
   *  question is tagged against — the first is a heading. */
  isRevisable: boolean;
}

export interface PaperDocument {
  docType: PaperDocType;
  pageCount: number | null;
}

export interface Paper {
  slug: string;
  subjectSlug: string;
  year: number;
  season: Season;
  component: number;
  variant: number | null;
  questionType: QuestionType;
  questionCount: number;
  documents: PaperDocument[];
}

export interface McqQuestion {
  id: string;
  paperSlug: string;
  displayLabel: string;
  questionText: string;
  options: Record<McqOption, string>;
  correctOption: McqOption;
  /** Grounded in the mark scheme; the examiner note is what the report says
   *  students actually got wrong. Both are shown only after answering.
   *
   *  Null for a multiple-choice question whose mark scheme is nothing but the
   *  answer grid — which is most of them. Repeating the letter under the
   *  heading "Mark scheme" would teach a student to expect an explanation and
   *  then hand them the thing they already knew. */
  markScheme: string | null;
  examinerNote: string | null;
  topicCodes: string[];
  /** The question as printed. For a multiple-choice question ingested
   *  geometrically this is the *only* faithful rendering: the text is not
   *  extracted, and the options are frequently diagrams. Null until the crop
   *  has been uploaded. */
  cropUrl: string | null;
  /** Every other sitting that shares this question verbatim — CAIE reuses
   *  most MCQs between one session's variants (component 11 and 12, most
   *  often). Empty for a question nothing else repeats. Populated by the
   *  pipeline's `dedupe` command, not derived here. */
  alsoIn: DuplicateRef[];
}

/** One leaf under a structured question, in paper order — the unit a student
 *  actually self-marks against, the same reason `ReviewPart` exists for the
 *  reviewer. `maxMarks`/`markSchemeText` are null only when the mark scheme
 *  never matched this label (see the review queue's `unmatched_mark_scheme`)
 *  and a reviewer approved the question anyway; the arena shows it but
 *  cannot ask the student to self-mark it. */
export interface StructuredPart {
  displayLabel: string;
  maxMarks: number | null;
  markSchemeText: string | null;
}

/** One page of a structured question's crop, in reading order. Always more
 *  than one for a question that runs across a page break — CAIE's Section B
 *  routinely does — since a figure on an earlier page can be what a later
 *  part refers back to. */
export interface StructuredQuestionCrop {
  pageNumber: number;
  cropUrl: string | null;
}

export interface StructuredQuestion {
  id: string;
  paperSlug: string;
  displayLabel: string;
  /** Real, unlike an mcq's: a structured question's own text is read
   *  directly off the page during segmentation, not left for a later pass.
   *  Can still be empty — a question that opens straight into "(a)" with no
   *  introductory text of its own. */
  questionText: string;
  crops: StructuredQuestionCrop[];
  parts: StructuredPart[];
}

export interface DuplicateRef {
  paperSlug: string;
  displayLabel: string;
  year: number;
  season: Season;
  component: number;
  variant: number | null;
}

export const SEASON_LABELS: Record<Season, string> = {
  feb_march: "Feb / March",
  may_june: "May / June",
  oct_nov: "Oct / Nov",
};

export const DOC_LABELS: Record<PaperDocType, string> = {
  qp: "Question Paper",
  ms: "Mark Scheme",
  er: "Examiner Report",
  gt: "Grade Thresholds",
  in: "Insert",
};

export function paperName(paper: Paper): string {
  const variant = paper.variant === null ? "" : String(paper.variant);
  return `Paper ${paper.component}${variant}`;
}

export function sessionName(paper: Paper): string {
  return `${SEASON_LABELS[paper.season]} ${paper.year}`;
}

export function duplicateRefLabel(ref: DuplicateRef): string {
  const variant = ref.variant === null ? "" : String(ref.variant);
  return `${SEASON_LABELS[ref.season]} ${ref.year} Paper ${ref.component}${variant}`;
}

/* ---------------------------------------------------------------------------
   Review queue
   --------------------------------------------------------------------------- */

/** Why the pipeline refused to publish a question on its own.
 *
 *  Each value corresponds to a specific check in pipeline/: the cross-check
 *  against the PDF text layer, the bbox sanity check, the mark-scheme matcher,
 *  and the tagging confidence floor. Showing the reviewer *which* check fired is
 *  what makes a queue of thousands tractable — they know what to look at. */
export type ReviewFlag =
  | "low_tag_confidence"
  | "text_layer_mismatch"
  | "bbox_outside_page"
  | "unmatched_mark_scheme"
  | "ambiguous_mark_scheme"
  | "no_text_layer";

export const REVIEW_FLAG_LABELS: Record<ReviewFlag, string> = {
  low_tag_confidence: "Unsure of the topic",
  text_layer_mismatch: "Label not found in the page text",
  bbox_outside_page: "Crop falls outside the page",
  unmatched_mark_scheme: "No mark scheme matched",
  ambiguous_mark_scheme: "Two mark scheme entries claimed this",
  no_text_layer: "Scanned page — no text to cross-check",
};

/** What the reviewer needs to look at, and what to check it against. */
export const REVIEW_FLAG_HINTS: Record<ReviewFlag, string> = {
  low_tag_confidence:
    "The classifier could not confidently place this. Pick the topic whose learning objective it actually tests.",
  text_layer_mismatch:
    "The model reported a question number that does not appear in the page's own text. Check it is not invented.",
  bbox_outside_page:
    "The crop region ran past the page edge and was clamped. Confirm nothing is cut off.",
  unmatched_mark_scheme:
    "No mark scheme entry matched this label. Attach one, or reject if the question was mis-segmented.",
  ambiguous_mark_scheme:
    "Two entries claimed this question, so both were withheld rather than guessed at. Choose the right one.",
  no_text_layer:
    "Scanned page: the extraction had no text layer to check itself against, so read it carefully.",
};

export interface ProposedTopic {
  code: string;
  title: string;
  confidence: number;
  reasoning: string;
}

/** One page of a question's crop. A multiple-choice question is always one;
 *  a structured question can run to several — CAIE routinely carries a
 *  question across a page break, and the reviewer needs to see all of it,
 *  not just where it starts. */
export interface ReviewCrop {
  storageKey: string;
  pageNumber: number;
  /** Signed at render time. The queue shows unapproved questions, which
   *  /api/asset deliberately refuses, so these are signed directly instead. */
  url: string | null;
}

/** One leaf under a structured question — "9(a)(ii)", not "9" or "9(a)" — the
 *  unit marks and a mark scheme entry actually attach to. Absent on an mcq
 *  item, where the row already is the whole practice unit. */
export interface ReviewPart {
  displayLabel: string;
  maxMarks: number | null;
  markSchemeText: string | null;
  reviewFlags: ReviewFlag[];
}

export interface ReviewItem {
  id: string;
  paperSlug: string;
  paperTitle: string;
  displayLabel: string;
  questionType: QuestionType;
  questionText: string;
  /** An mcq's answer-grid entry. Null for a structured question, whose marking
   *  lives on `parts` instead — an mcq's marking never does, so the two never
   *  need to be read together. */
  markScheme: string | null;
  correctOption: McqOption | null;
  crops: ReviewCrop[];
  /** Non-null only for a structured question — its leaves, in paper order.
   *  Null, not empty, for an mcq: "no parts" and "not a structured question"
   *  are different facts, and the card renders them differently. */
  parts: ReviewPart[] | null;
  extractionConfidence: number;
  flags: ReviewFlag[];
  proposedTopics: ProposedTopic[];
}

export type ReviewDecision = "approved" | "rejected";

/** A question that has already left the queue — approved or rejected — for
 *  the admin lookup that finds it again afterward. `v_review_queue` only ever
 *  shows what is still pending, so once a question is decided this is the
 *  only place it is still listed. */
export interface DecidedItem {
  id: string;
  paperSlug: string;
  paperTitle: string;
  displayLabel: string;
  decision: ReviewDecision;
  reviewedAt: string | null;
  primaryTopic: { code: string; title: string; confidence: number } | null;
}

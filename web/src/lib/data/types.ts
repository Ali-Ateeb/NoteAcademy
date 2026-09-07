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
   *  students actually got wrong. Both are shown only after answering. */
  markScheme: string;
  examinerNote: string | null;
  topicCodes: string[];
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

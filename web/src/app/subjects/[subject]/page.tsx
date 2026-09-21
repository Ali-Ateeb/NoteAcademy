import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getPapersByYear,
  getPlayablePapers,
  getSubject,
  getSubjects,
  getTopics,
} from "@/lib/data/catalog";
import { paperName, SEASON_LABELS, type Topic } from "@/lib/data/types";

type Params = { params: Promise<{ subject: string }> };

// ISR fallback for approvals that bypass the web app entirely (pipeline
// bulk-approve, writing straight to Postgres) — see papers/[paper]/page.tsx
// for the full reasoning. The primary path, /api/review's revalidatePath
// calls, updates this page immediately; this is what still catches the rest.
export const revalidate = 900;

/** Every subject page is statically generated. The paper index is the entire
 *  organic acquisition channel, so it has to be crawlable and instant. */
export async function generateStaticParams() {
  const subjects = await getSubjects();
  return subjects.filter((s) => s.isPublished).map((s) => ({ subject: s.slug }));
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { subject: slug } = await params;
  const subject = await getSubject(slug);
  if (!subject) return {};

  return {
    title: `${subject.title} ${subject.syllabusCode} past papers`,
    description: `Every ${subject.title} ${subject.syllabusCode} past paper with mark schemes, plus topical questions and timed Paper 1 practice.`,
  };
}

export default async function SubjectPage({ params }: Params) {
  const { subject: slug } = await params;
  const subject = await getSubject(slug);
  if (!subject || !subject.isPublished) notFound();

  const [years, topics, playable] = await Promise.all([
    getPapersByYear(slug),
    getTopics(slug),
    getPlayablePapers(slug),
  ]);

  return (
    <div className="mx-auto max-w-6xl px-5 py-12">
      <nav className="mb-6 text-sm text-ink-3">
        <Link href="/subjects" className="transition-colors hover:text-ink">
          Subjects
        </Link>
        <span className="mx-2">/</span>
        <span className="text-ink-2">{subject.title}</span>
      </nav>

      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="font-serif text-4xl font-extrabold tracking-tight text-ink sm:text-5xl">
          {subject.title}
        </h1>
        <span className="hl hl-blue font-mono text-sm">{subject.syllabusCode}</span>
      </div>
      <p className="mt-3 max-w-2xl leading-relaxed text-ink-2">{subject.description}</p>

      <HighestValueTopics subject={slug} topics={topics} />

      {playable.length > 0 && (
        <section className="mt-10 rounded-[2rem] bg-periwinkle p-6 sm:p-8">
          <h2 className="text-2xl font-extrabold text-ink">Timed Paper 1 practice</h2>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-ink">
            Sit a multiple-choice paper under exam conditions. Progress is saved as
            you go, so a closed tab does not cost you the attempt.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {playable.map((paper) => (
              <Link
                key={paper.slug}
                href={`/practice/${paper.slug}`}
                className="pill pill-plain px-4 py-2 text-sm"
              >
                {SEASON_LABELS[paper.season]} {paper.year} · {paperName(paper)}
              </Link>
            ))}
          </div>
        </section>
      )}

      <div className="mt-12 grid gap-12 lg:grid-cols-[1fr_320px]">
        <section>
          <h2 className="rule-under font-serif text-2xl tracking-tight text-ink">By year</h2>
          <div className="mt-5 space-y-7">
            {years.map(({ year, papers }) => (
              <div key={year}>
                <h3 className="mb-3">
                  <span className="hl hl-yellow font-mono text-sm">{year}</span>
                </h3>
                <div className="grid gap-2 sm:grid-cols-2">
                  {papers.map((paper) => (
                    <Link
                      key={paper.slug}
                      href={`/papers/${paper.slug}`}
                      className="lift flex items-center justify-between rounded-xl border border-line bg-surface px-4 py-3 shadow-card"
                    >
                      <span className="text-sm text-ink">
                        <span className="font-bold">{paperName(paper)}</span>
                        <span className="ml-2 text-ink-3">
                          {SEASON_LABELS[paper.season]}
                        </span>
                      </span>
                      <span className="flex gap-1">
                        {paper.documents.map((doc) => (
                          <span
                            key={doc.docType}
                            className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] uppercase text-ink-3"
                          >
                            {doc.docType}
                          </span>
                        ))}
                      </span>
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>

        <aside>
          <h2 className="rule-under font-serif text-2xl tracking-tight text-ink">By topic</h2>
          <p className="mt-2 text-sm leading-relaxed text-ink-2">
            Questions from every year, grouped by what they test.
          </p>
          {/* The syllabus as printed. A CAIE tree is three deep and mixes
              headings with revisable units — '4.2 Electrical quantities'
              groups '4.2.1'–'4.2.4' and has no outcomes of its own — so a flat
              list of eighty-three rows is both longer and less recognisable
              than the contents page a student already knows. Headings are
              text; only the units are links. */}
          <div className="mt-5 space-y-4">
            {topics
              .filter((topic) => topic.parentCode === null)
              .map((section) => (
                <div key={section.code}>
                  <p className="mb-1.5 text-xs font-bold uppercase tracking-widest text-ink-2">
                    <span className="mr-2 font-mono normal-case">{section.code}</span>
                    {section.title}
                  </p>
                  <div className="space-y-1">
                    {descendantsOf(section.code, topics).map((topic) => (
                      <TopicRow key={topic.code} subject={slug} topic={topic} />
                    ))}
                  </div>
                </div>
              ))}
          </div>
        </aside>
      </div>
    </div>
  );
}

/** A section's nodes, in document order, with containers kept in place.
 *
 *  Containers are rendered as sub-headings rather than dropped: '4.2' is how
 *  the syllabus groups four units a student thinks of together, and removing it
 *  leaves four rows that look unrelated. */
function descendantsOf(sectionCode: string, topics: Topic[]): Topic[] {
  return topics.filter((topic) => topic.code.startsWith(`${sectionCode}.`));
}

// How many of the highest-value topics to surface — a shortlist worth
// actually reading, not a re-sorted copy of the full syllabus tree below.
const HIGHEST_VALUE_COUNT = 5;

/** Where the marks actually are, historically — not just where the
 *  questions are. Two topics with the same question count are not equally
 *  worth revising if one is all 1-mark MCQs and the other all 6-mark
 *  structured parts; `totalMarks` (0031_topic_marks.sql) is the number that
 *  tells them apart, and it is exactly the kind of CAIE-specific signal a
 *  generic revision app has no way to compute. */
function HighestValueTopics({ subject, topics }: { subject: string; topics: Topic[] }) {
  const ranked = topics
    .filter((topic) => topic.isRevisable && topic.totalMarks > 0)
    .sort((a, b) => b.totalMarks - a.totalMarks)
    .slice(0, HIGHEST_VALUE_COUNT);

  if (ranked.length === 0) return null;

  return (
    <section className="note-sheet mt-8 py-7 pr-6 pl-14">
      {/* 28px line-height throughout: every line of text sits on a ruled line. */}
      <h2 className="text-lg leading-7 text-ink">
        <span className="hl hl-yellow">Where the marks actually are</span>
      </h2>
      <p className="max-w-xl text-sm leading-7 text-ink-2">
        Ranked by marks earned across every past paper, not just how many questions
        mention them — a topic tested by six-mark structured parts outweighs one tested
        only by 1-mark MCQs.
      </p>
      <div className="flex flex-wrap gap-x-2 gap-y-[14px] pt-[7px]">
        {ranked.map((topic) => (
          <Link
            key={topic.code}
            href={`/topics/${subject}/${topic.slug}`}
            className="pill pill-plain gap-2 px-3.5 py-1.5 text-sm"
          >
            <span>{topic.title}</span>
            <span className="font-mono text-xs font-normal opacity-70">{topic.totalMarks} marks</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

function TopicRow({ subject, topic }: { subject: string; topic: Topic }) {
  const indent = topic.code.split(".").length > 2 ? "ml-3" : "";

  if (!topic.isRevisable) {
    return (
      <p className={`${indent} pt-1.5 text-xs text-ink-3`}>
        <span className="mr-2 font-mono">{topic.code}</span>
        {topic.title}
      </p>
    );
  }

  return (
    <Link
      href={`/topics/${subject}/${topic.slug}`}
      className={`${indent} lift flex items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-card`}
    >
      <span className="text-ink">
        <span className="mr-2 font-mono text-xs text-ink-3">{topic.code}</span>
        {topic.title}
      </span>
      <span className="font-mono text-xs text-ink-3">{topic.questionCount}</span>
    </Link>
  );
}

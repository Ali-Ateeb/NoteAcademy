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
    description: `Every ${subject.title} ${subject.syllabusCode} past paper with mark schemes and examiner reports, plus topical questions and timed Paper 1 practice.`,
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
        <h1 className="font-serif text-4xl tracking-tight text-ink">{subject.title}</h1>
        <span className="rounded-md bg-surface-2 px-2 py-1 font-mono text-sm text-ink-2">
          {subject.syllabusCode}
        </span>
      </div>
      <p className="mt-3 max-w-2xl leading-relaxed text-ink-2">{subject.description}</p>

      {playable.length > 0 && (
        <section className="mt-10 rounded-2xl border border-line bg-accent-soft p-6">
          <h2 className="font-medium tracking-tight text-ink">Timed Paper 1 practice</h2>
          <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-ink-2">
            Sit a multiple-choice paper under exam conditions. Progress is saved as
            you go, so a closed tab does not cost you the attempt.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {playable.map((paper) => (
              <Link
                key={paper.slug}
                href={`/practice/${paper.slug}`}
                className="rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90"
              >
                {SEASON_LABELS[paper.season]} {paper.year} · {paperName(paper)}
              </Link>
            ))}
          </div>
        </section>
      )}

      <div className="mt-12 grid gap-12 lg:grid-cols-[1fr_320px]">
        <section>
          <h2 className="font-serif text-2xl tracking-tight text-ink">By year</h2>
          <div className="mt-5 space-y-7">
            {years.map(({ year, papers }) => (
              <div key={year}>
                <h3 className="mb-2.5 font-mono text-sm text-ink-3">{year}</h3>
                <div className="grid gap-2 sm:grid-cols-2">
                  {papers.map((paper) => (
                    <Link
                      key={paper.slug}
                      href={`/papers/${paper.slug}`}
                      className="flex items-center justify-between rounded-xl border border-line bg-surface px-4 py-3 shadow-card transition-colors hover:border-line-strong"
                    >
                      <span className="text-sm text-ink">
                        <span className="font-medium">{paperName(paper)}</span>
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
          <h2 className="font-serif text-2xl tracking-tight text-ink">By topic</h2>
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
                  <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-ink-3">
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
      className={`${indent} flex items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-card transition-colors hover:border-line-strong`}
    >
      <span className="text-ink">
        <span className="mr-2 font-mono text-xs text-ink-3">{topic.code}</span>
        {topic.title}
      </span>
      <span className="font-mono text-xs text-ink-3">{topic.questionCount}</span>
    </Link>
  );
}

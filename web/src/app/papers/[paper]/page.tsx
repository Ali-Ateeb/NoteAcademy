import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { SplitViewer } from "@/components/SplitViewer";
import { getPaper, getPapers, getSubject, getSubjects } from "@/lib/data/catalog";
import { paperName, sessionName } from "@/lib/data/types";

type Params = { params: Promise<{ paper: string }> };

/** Statically generated, one page per paper. "5054 may june 2019 paper 12" is a
 *  real search query — tens of thousands of indexable pages is the marketing
 *  budget for a product like this. */
export async function generateStaticParams() {
  const subjects = await getSubjects();
  const slugs: { paper: string }[] = [];
  for (const subject of subjects.filter((s) => s.isPublished)) {
    for (const paper of await getPapers(subject.slug)) {
      slugs.push({ paper: paper.slug });
    }
  }
  return slugs;
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { paper: slug } = await params;
  const paper = await getPaper(slug);
  if (!paper) return {};
  const subject = await getSubject(paper.subjectSlug);
  if (!subject) return {};

  const title = `${subject.title} ${subject.syllabusCode} ${sessionName(paper)} ${paperName(paper)}`;
  return {
    title,
    description: `${title} — question paper, mark scheme and examiner report, side by side.`,
  };
}

export default async function PaperPage({ params }: Params) {
  const { paper: slug } = await params;
  const paper = await getPaper(slug);
  if (!paper) notFound();

  const subject = await getSubject(paper.subjectSlug);
  if (!subject) notFound();

  const isPlayable = paper.questionType === "mcq";

  return (
    <div className="mx-auto max-w-6xl px-5 py-10">
      <nav className="mb-5 text-sm text-ink-3">
        <Link href="/subjects" className="transition-colors hover:text-ink">
          Subjects
        </Link>
        <span className="mx-2">/</span>
        <Link
          href={`/subjects/${subject.slug}`}
          className="transition-colors hover:text-ink"
        >
          {subject.title}
        </Link>
        <span className="mx-2">/</span>
        <span className="text-ink-2">{paperName(paper)}</span>
      </nav>

      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl tracking-tight text-ink">
            {subject.title} {paperName(paper)}
          </h1>
          <p className="mt-1.5 text-ink-2">
            {sessionName(paper)}
            <span className="mx-2 text-ink-3">·</span>
            <span className="font-mono text-sm">{subject.syllabusCode}</span>
            <span className="mx-2 text-ink-3">·</span>
            {paper.questionCount} questions
          </p>
        </div>

        {isPlayable && (
          <Link
            href={`/practice/${paper.slug}`}
            className="rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90"
          >
            Sit this paper →
          </Link>
        )}
      </div>

      <SplitViewer
        paperSlug={paper.slug}
        available={paper.documents.map((doc) => doc.docType)}
      />
    </div>
  );
}

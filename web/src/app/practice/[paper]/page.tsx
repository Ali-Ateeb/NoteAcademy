import type { Metadata, Route } from "next";
import { notFound } from "next/navigation";

import { McqArena } from "@/components/McqArena";
import {
  getMcqQuestions,
  getPaper,
  getPlayablePapers,
  getSubject,
  getSubjects,
} from "@/lib/data/catalog";
import { paperName, sessionName } from "@/lib/data/types";

type Params = { params: Promise<{ paper: string }> };

export async function generateStaticParams() {
  const subjects = await getSubjects();
  const slugs: { paper: string }[] = [];
  for (const subject of subjects.filter((s) => s.isPublished)) {
    for (const paper of await getPlayablePapers(subject.slug)) {
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

  return {
    title: `Practise ${subject.title} ${sessionName(paper)} ${paperName(paper)}`,
    description: "Timed, resumable multiple-choice practice with instant marking.",
  };
}

export default async function PracticePage({ params }: Params) {
  const { paper: slug } = await params;
  const paper = await getPaper(slug);
  if (!paper || paper.questionType !== "mcq") notFound();

  const [subject, questions] = await Promise.all([
    getSubject(paper.subjectSlug),
    getMcqQuestions(slug),
  ]);
  if (!subject || questions.length === 0) notFound();

  const title = `${subject.title} ${subject.syllabusCode} · ${sessionName(paper)} · ${paperName(paper)}`;

  return (
    <div className="mx-auto max-w-4xl px-5 py-10">
      <h1 className="mb-1 font-serif text-2xl tracking-tight text-ink">{title}</h1>
      <p className="mb-7 text-sm text-ink-2">
        {questions.length} questions · answers are saved as you go
      </p>
      <McqArena
        sessionKey={slug}
        title={title}
        questions={questions}
        backHref={`/papers/${slug}` as Route}
        backLabel="Leave test"
      />
    </div>
  );
}

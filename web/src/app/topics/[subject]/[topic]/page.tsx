import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getQuestionsByTopic,
  getSubject,
  getSubjects,
  getTopic,
  getTopics,
} from "@/lib/data/catalog";

type Params = { params: Promise<{ subject: string; topic: string }> };

export async function generateStaticParams() {
  const subjects = await getSubjects();
  const params: { subject: string; topic: string }[] = [];
  for (const subject of subjects.filter((s) => s.isPublished)) {
    for (const topic of await getTopics(subject.slug)) {
      params.push({ subject: subject.slug, topic: topic.slug });
    }
  }
  return params;
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { subject: subjectSlug, topic: topicSlug } = await params;
  const [subject, topic] = await Promise.all([
    getSubject(subjectSlug),
    getTopic(subjectSlug, topicSlug),
  ]);
  if (!subject || !topic) return {};

  return {
    title: `${topic.title} — ${subject.title} ${subject.syllabusCode} topical questions`,
    description: `Every ${subject.title} past-paper question on ${topic.title}, with mark schemes.`,
  };
}

export default async function TopicPage({ params }: Params) {
  const { subject: subjectSlug, topic: topicSlug } = await params;
  const [subject, topic] = await Promise.all([
    getSubject(subjectSlug),
    getTopic(subjectSlug, topicSlug),
  ]);
  if (!subject || !topic) notFound();

  const questions = await getQuestionsByTopic(subjectSlug, topic.code);

  return (
    <div className="mx-auto max-w-4xl px-5 py-12">
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
        <span className="text-ink-2">{topic.title}</span>
      </nav>

      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="font-serif text-3xl tracking-tight text-ink">{topic.title}</h1>
        <span className="font-mono text-sm text-ink-3">{topic.code}</span>
      </div>

      <div className="mt-6 rounded-2xl border border-line bg-surface p-5 shadow-card">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-ink-3">
          What the syllabus asks of you
        </h2>
        <ul className="mt-3 space-y-1.5">
          {topic.learningObjectives.map((objective) => (
            <li key={objective} className="flex gap-2.5 text-sm text-ink-2">
              <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-accent" />
              {objective}
            </li>
          ))}
        </ul>
      </div>

      <h2 className="mb-4 mt-10 font-serif text-2xl tracking-tight text-ink">
        {questions.length} question{questions.length === 1 ? "" : "s"}
      </h2>

      <div className="space-y-3">
        {questions.map((question) => (
          <details
            key={question.id}
            className="group rounded-2xl border border-line bg-surface p-5 shadow-card"
          >
            <summary className="cursor-pointer list-none">
              <span className="text-sm leading-relaxed text-ink">
                {question.questionText}
              </span>
              <span className="mt-2 block text-xs text-ink-3 group-open:hidden">
                Show mark scheme →
              </span>
            </summary>

            <div className="mt-4 space-y-1.5">
              {(["A", "B", "C", "D"] as const).map((option) => (
                <div
                  key={option}
                  className={`flex gap-3 rounded-lg px-3 py-2 text-sm ${
                    option === question.correctOption
                      ? "bg-correct-soft text-ink"
                      : "text-ink-2"
                  }`}
                >
                  <span className="font-mono text-xs text-ink-3">{option}</span>
                  {question.options[option]}
                </div>
              ))}
            </div>

            <div className="mt-4 rounded-xl bg-surface-2 p-4">
              <p className="text-xs font-semibold uppercase tracking-widest text-ink-3">
                Mark scheme
              </p>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
                {question.markScheme}
              </p>
              {question.examinerNote && (
                <>
                  <p className="mt-4 text-xs font-semibold uppercase tracking-widest text-marks">
                    Examiner report
                  </p>
                  <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
                    {question.examinerNote}
                  </p>
                </>
              )}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}

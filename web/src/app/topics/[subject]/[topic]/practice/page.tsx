import type { Metadata, Route } from "next";
import { notFound } from "next/navigation";

import { McqArena } from "@/components/McqArena";
import {
  getQuestionsByTopic,
  getSubject,
  getSubjects,
  getTopic,
  revisableTopics,
} from "@/lib/data/catalog";

type Params = { params: Promise<{ subject: string; topic: string }> };

export async function generateStaticParams() {
  const subjects = await getSubjects();
  const params: { subject: string; topic: string }[] = [];
  for (const subject of subjects.filter((s) => s.isPublished)) {
    for (const topic of await revisableTopics(subject.slug)) {
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
    title: `Drill ${topic.title} — ${subject.title} ${subject.syllabusCode}`,
    description: `Practise every ${subject.title} past-paper question on ${topic.title}, untimed, with mark schemes.`,
  };
}

export default async function TopicDrillPage({ params }: Params) {
  const { subject: subjectSlug, topic: topicSlug } = await params;
  const [subject, topic] = await Promise.all([
    getSubject(subjectSlug),
    getTopic(subjectSlug, topicSlug),
  ]);
  if (!subject || !topic) notFound();

  const questions = await getQuestionsByTopic(subjectSlug, topic.code);
  if (questions.length === 0) notFound();

  return (
    <div className="mx-auto max-w-4xl px-5 py-10">
      <h1 className="mb-1 font-serif text-2xl tracking-tight text-ink">
        {topic.title}
      </h1>
      <p className="mb-7 text-sm text-ink-2">
        {questions.length} question{questions.length === 1 ? "" : "s"} from every
        year · untimed
      </p>
      {/* Untimed by design. Drilling a weak topic is about getting it right;
          a countdown adds pressure to precisely the thing the student is
          already worst at. The timed run is the paper, not the drill. */}
      <McqArena
        sessionKey={`topic:${subjectSlug}:${topicSlug}`}
        title={`${subject.title} · ${topic.title}`}
        questions={questions}
        backHref={`/topics/${subjectSlug}/${topicSlug}` as Route}
        backLabel="Back to topic"
        secondsPerQuestion={null}
      />
    </div>
  );
}

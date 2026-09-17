import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ReviseWeakTopics } from "@/components/ReviseWeakTopics";
import { getQuestionTopicsForSubject, getSubject, getTopics } from "@/lib/data/catalog";

type Params = { params: Promise<{ subject: string }> };

// Not statically generated, unlike a topic's own drill page: which topics are
// weak is per student, computed client-side from their own attempts, so
// there is no single version of this page to pre-render.
export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { subject: subjectSlug } = await params;
  const subject = await getSubject(subjectSlug);
  if (!subject) return {};

  return {
    title: `Revise weak topics — ${subject.title}`,
    description: `A drill built from your weakest ${subject.title} topics, worst first.`,
  };
}

export default async function ReviseWeakTopicsPage({ params }: Params) {
  const { subject: subjectSlug } = await params;
  const subject = await getSubject(subjectSlug);
  if (!subject || !subject.isPublished) notFound();

  const [topics, questionTopics] = await Promise.all([
    getTopics(subjectSlug),
    getQuestionTopicsForSubject(subjectSlug),
  ]);

  return (
    <div className="mx-auto max-w-4xl px-5 py-10">
      <ReviseWeakTopics subject={subject} topics={topics} questionTopics={questionTopics} />
    </div>
  );
}

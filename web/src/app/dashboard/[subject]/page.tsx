import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Dashboard } from "@/components/Dashboard";
import { getQuestionTopicsForSubject, getSubject, getSubjects, getTopics } from "@/lib/data/catalog";

type Params = { params: Promise<{ subject: string }> };

export async function generateStaticParams() {
  const subjects = await getSubjects();
  return subjects.filter((s) => s.isPublished).map((s) => ({ subject: s.slug }));
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { subject: subjectSlug } = await params;
  const subject = await getSubject(subjectSlug);
  if (!subject) return {};

  return {
    title: `${subject.title} dashboard`,
    description: `Accuracy per topic and time per question across your ${subject.title} attempts.`,
  };
}

export default async function SubjectDashboardPage({ params }: Params) {
  const { subject: subjectSlug } = await params;
  const subject = await getSubject(subjectSlug);
  if (!subject || !subject.isPublished) notFound();

  const [topics, questionTopics] = await Promise.all([
    getTopics(subjectSlug),
    getQuestionTopicsForSubject(subjectSlug),
  ]);

  return (
    <div className="mx-auto max-w-4xl px-5 py-12">
      <nav className="mb-5 text-sm text-ink-3">
        <Link href="/dashboard" className="transition-colors hover:text-ink">
          Dashboard
        </Link>
        <span className="mx-2">/</span>
        <span className="text-ink-2">{subject.title}</span>
      </nav>

      <h1 className="font-serif text-4xl tracking-tight text-ink">
        {subject.title} — your weak areas
      </h1>
      <p className="mt-3 max-w-xl leading-relaxed text-ink-2">
        Accuracy per topic, worst first. Revision goes where it is needed, not
        where it is comfortable.
      </p>
      <Dashboard subjectSlug={subjectSlug} topics={topics} questionTopics={questionTopics} />
    </div>
  );
}

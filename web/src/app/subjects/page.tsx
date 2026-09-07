import type { Metadata } from "next";
import Link from "next/link";

import { getLevels, getSubjects } from "@/lib/data/catalog";

export const metadata: Metadata = {
  title: "Subjects",
  description:
    "Cambridge O Level, IGCSE and A Level subjects with complete past-paper question banks.",
};

export default async function SubjectsPage() {
  const [levels, subjects] = await Promise.all([getLevels(), getSubjects()]);

  return (
    <div className="mx-auto max-w-6xl px-5 py-14">
      <h1 className="font-serif text-4xl tracking-tight text-ink">Subjects</h1>
      <p className="mt-3 max-w-xl leading-relaxed text-ink-2">
        Each subject is published only once its question bank has been reviewed.
      </p>

      <div className="mt-12 space-y-12">
        {levels.map((level) => {
          const levelSubjects = subjects.filter((s) => s.levelCode === level.code);
          if (levelSubjects.length === 0) return null;

          return (
            <section key={level.code}>
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-ink-3">
                {level.name}
              </h2>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {levelSubjects.map((subject) =>
                  subject.isPublished ? (
                    <Link
                      key={subject.slug}
                      href={`/subjects/${subject.slug}`}
                      className="group rounded-2xl border border-line bg-surface p-5 shadow-card transition-all hover:border-line-strong hover:shadow-lift"
                    >
                      <div className="flex items-baseline justify-between">
                        <span className="font-medium tracking-tight text-ink">
                          {subject.title}
                        </span>
                        <span className="font-mono text-xs text-ink-3">
                          {subject.syllabusCode}
                        </span>
                      </div>
                      <p className="mt-2 text-sm leading-relaxed text-ink-2">
                        {subject.description}
                      </p>
                      <span className="mt-4 inline-block text-sm font-medium text-accent">
                        Open →
                      </span>
                    </Link>
                  ) : (
                    <div
                      key={subject.slug}
                      className="rounded-2xl border border-dashed border-line p-5"
                    >
                      <div className="flex items-baseline justify-between">
                        <span className="font-medium tracking-tight text-ink-3">
                          {subject.title}
                        </span>
                        <span className="font-mono text-xs text-ink-3">
                          {subject.syllabusCode}
                        </span>
                      </div>
                      <p className="mt-2 text-sm text-ink-3">Ingestion in progress</p>
                    </div>
                  ),
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

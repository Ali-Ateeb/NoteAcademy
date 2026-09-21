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
      <h1 className="rule-under font-serif text-4xl font-extrabold tracking-tight text-ink sm:text-5xl">Subjects</h1>
      <p className="mt-3 max-w-xl leading-relaxed text-ink-2">
        Each subject is published only once its question bank has been reviewed.
      </p>

      <div className="mt-12 space-y-12">
        {levels.map((level) => {
          const levelSubjects = subjects.filter((s) => s.levelCode === level.code);
          if (levelSubjects.length === 0) return null;

          return (
            <section key={level.code}>
              <h2 className="mb-4">
                <span className="hl hl-yellow text-sm font-bold uppercase tracking-widest">
                  {level.name}
                </span>
              </h2>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {levelSubjects.map((subject) =>
                  subject.isPublished ? (
                    <Link
                      key={subject.slug}
                      href={`/subjects/${subject.slug}`}
                      className="lift group rounded-2xl border-2 border-edge bg-surface p-5 shadow-[4px_4px_0_var(--pop)]"
                    >
                      <div className="flex items-baseline justify-between">
                        <span className="text-lg font-bold text-ink">{subject.title}</span>
                        <span className="hl hl-blue font-mono text-xs">{subject.syllabusCode}</span>
                      </div>
                      <p className="mt-2 text-sm leading-relaxed text-ink-2">
                        {subject.description}
                      </p>
                      <span className="mt-4 inline-block text-sm font-bold text-accent">
                        Open →
                      </span>
                    </Link>
                  ) : (
                    <div
                      key={subject.slug}
                      className="rounded-2xl border-2 border-dashed border-line-strong bg-surface/70 p-5"
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

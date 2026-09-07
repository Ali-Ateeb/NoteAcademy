import Link from "next/link";

import { getSubjects } from "@/lib/data/catalog";

const FEATURES = [
  {
    title: "Every question, tagged to your syllabus",
    body: "Papers are split into individual questions and mapped to the learning outcomes of the syllabus version you are actually sitting — so a 2014 question on a withdrawn topic never reaches you.",
  },
  {
    title: "Mark scheme beside the question",
    body: "The official marking points and the chief examiner's comments, attached to the question they belong to. Not a separate PDF you open in another tab.",
  },
  {
    title: "Paper 1 as a real test",
    body: "Timed, resumable, marked instantly, with the examiner's note on what most candidates got wrong — shown after you answer, not before.",
  },
  {
    title: "Weak areas, not vanity metrics",
    body: "Accuracy per topic and time per question, so revision goes where it is needed instead of where it is comfortable.",
  },
];

export default async function HomePage() {
  const subjects = await getSubjects();
  const published = subjects.filter((s) => s.isPublished);

  return (
    <>
      <section className="mx-auto max-w-6xl px-5 pt-20 pb-16 sm:pt-28">
        <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-ink-2">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          O Level Physics 5054 — complete
        </p>

        <h1 className="max-w-3xl text-balance font-serif text-5xl leading-[1.05] tracking-tight text-ink sm:text-6xl">
          Past papers that know
          <br />
          what they are testing.
        </h1>

        <p className="mt-6 max-w-xl text-lg leading-relaxed text-ink-2">
          Every Cambridge past paper, split question by question, tagged to your
          syllabus, with the mark scheme and examiner report attached to each one.
        </p>

        <div className="mt-9 flex flex-wrap gap-3">
          <Link
            href="/subjects"
            className="rounded-xl bg-accent px-5 py-3 text-sm font-medium text-accent-ink shadow-lift transition-opacity hover:opacity-90"
          >
            Browse subjects
          </Link>
          <Link
            href="/practice/physics-5054-2019-may-june-p12"
            className="rounded-xl border border-line-strong bg-surface px-5 py-3 text-sm font-medium text-ink transition-colors hover:bg-surface-2"
          >
            Try a Paper 1 test
          </Link>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 pb-8">
        <div className="grid gap-px overflow-hidden rounded-2xl border border-line bg-line sm:grid-cols-2">
          {FEATURES.map((feature) => (
            <div key={feature.title} className="bg-surface p-7">
              <h2 className="font-medium tracking-tight text-ink">{feature.title}</h2>
              <p className="mt-2.5 text-sm leading-relaxed text-ink-2">{feature.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 py-14">
        <h2 className="font-serif text-2xl tracking-tight text-ink">Available now</h2>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-ink-2">
          One subject, done properly, beats forty done badly — a wrongly tagged
          question wastes the revision time it was supposed to save. Subjects are
          published as their question banks pass review.
        </p>

        <div className="mt-6 flex flex-wrap gap-2.5">
          {published.map((subject) => (
            <Link
              key={subject.slug}
              href={`/subjects/${subject.slug}`}
              className="rounded-xl border border-line bg-surface px-4 py-3 shadow-card transition-colors hover:border-line-strong"
            >
              <span className="font-medium text-ink">{subject.title}</span>
              <span className="ml-2 font-mono text-xs text-ink-3">
                {subject.syllabusCode}
              </span>
            </Link>
          ))}
          {subjects
            .filter((s) => !s.isPublished)
            .map((subject) => (
              <span
                key={subject.slug}
                className="rounded-xl border border-dashed border-line px-4 py-3 text-ink-3"
              >
                {subject.title}
                <span className="ml-2 font-mono text-xs">{subject.syllabusCode}</span>
                <span className="ml-2 text-xs">in progress</span>
              </span>
            ))}
        </div>
      </section>
    </>
  );
}

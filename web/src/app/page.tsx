import Link from "next/link";

import { LogoStacked, Stationery } from "@/components/Brand";
import { getSubjects } from "@/lib/data/catalog";

/* Four notes, one claim each. They are set on lined paper because that is the
 * product's own vocabulary — and because four numbered notes read as an
 * argument where four cards in a grid read as a feature list. */
const FEATURES = [
  {
    title: "Every question, tagged to your syllabus",
    body: "Papers are split into individual questions and mapped to the learning outcomes of the syllabus version you are actually sitting — so a 2014 question on a withdrawn topic never reaches you.",
    tilt: "-rotate-1",
    highlight: "hl-blue",
  },
  {
    title: "Mark scheme beside the question",
    body: "The official marking points and the chief examiner's comments, attached to the question they belong to. Not a separate PDF you open in another tab.",
    tilt: "rotate-1",
    highlight: "hl-yellow",
  },
  {
    title: "Paper 1 as a real test",
    body: "Timed, resumable, marked instantly, with the examiner's note on what most candidates got wrong — shown after you answer, not before.",
    tilt: "rotate-1",
    highlight: "hl-pink",
  },
  {
    title: "Weak areas, not vanity metrics",
    body: "Accuracy per topic and time per question, so revision goes where it is needed instead of where it is comfortable.",
    tilt: "-rotate-1",
    highlight: "hl-blue",
  },
];

export default async function HomePage() {
  const subjects = await getSubjects();
  const published = subjects.filter((s) => s.isPublished);
  const inProgress = subjects.filter((s) => !s.isPublished);

  return (
    <>
      <section className="relative mx-auto flex max-w-6xl flex-col items-center px-5 pt-14 pb-24 text-center sm:pt-20">
        {/* The desk. Four pieces of stationery, one in each corner, each on its
            own delay so they never bob in step. Hidden below sm where there is
            no margin to put them in. */}
        <Stationery item="calculator" delay={0} className="absolute top-24 left-4 hidden w-14 rotate-12 sm:block md:left-12 md:w-20 lg:left-24 lg:w-24" />
        <Stationery item="compass" delay={0.7} className="absolute top-20 right-4 hidden w-14 -rotate-12 sm:block md:right-12 md:w-20 lg:right-24 lg:w-24" />
        <Stationery item="pencil" delay={1.4} className="absolute bottom-24 left-4 hidden w-14 -rotate-6 sm:block md:left-12 md:w-20 lg:left-24 lg:w-24" />
        <Stationery item="ruler" delay={2.1} className="absolute right-4 bottom-20 hidden w-14 rotate-6 sm:block md:right-12 md:w-20 lg:right-24 lg:w-24" />

        <div className="animate-drop">
          <LogoStacked className="h-32 sm:h-40 lg:h-44" priority />
        </div>

        <h1
          className="animate-rise mt-8 max-w-3xl text-balance text-4xl leading-[1.12] font-extrabold text-ink sm:text-6xl"
          style={{ animationDelay: "120ms" }}
        >
          Past papers that know what they are{" "}
          {/* The one highlighted word on the page: the thing the whole product
              turns on. */}
          <span className="hl hl-yellow">testing.</span>
        </h1>

        <p
          className="animate-rise mt-6 max-w-xl text-lg leading-relaxed text-ink-2"
          style={{ animationDelay: "200ms" }}
        >
          Every Cambridge O Level past paper, split question by question, tagged to your
          syllabus, with the mark scheme and examiner report attached to each one.
        </p>

        <div
          className="animate-rise mt-9 flex flex-wrap items-center justify-center gap-3"
          style={{ animationDelay: "280ms" }}
        >
          <Link href="/subjects" className="pill pill-solid px-7 py-3 text-base">
            Browse subjects
          </Link>
          <Link
            href="/practice/physics-5054-2026-may-june-p11"
            className="pill group px-7 py-3 text-base"
          >
            Sit a Paper 1
            <span
              aria-hidden
              className="transition-transform duration-200 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:translate-x-1"
            >
              →
            </span>
          </Link>
        </div>
      </section>

      {/* The periwinkle card from the Note Academy site, holding the notes. */}
      <section className="mx-auto max-w-6xl px-3 sm:px-5">
        <div className="rounded-[2rem] bg-periwinkle px-5 py-12 sm:px-10 sm:py-16">
          <h2 className="mx-auto max-w-2xl text-center text-3xl font-extrabold text-ink sm:text-4xl">
            Built for how you actually revise
          </h2>

          <div className="stagger mt-12 grid gap-8 sm:grid-cols-2">
            {FEATURES.map((feature, i) => (
              <article
                key={feature.title}
                className={`note-sheet ${feature.tilt} py-7 pr-6 pl-[3.4rem] text-left transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:rotate-0 hover:-translate-y-1`}
              >
                {/* The line-height is the ruling: 28px, so every line of text
                    sits on a line of the paper. */}
                <span className="gutter-num absolute top-7 left-3 leading-7">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <h3 className="text-lg leading-7 text-ink">
                  <span className={`hl ${feature.highlight}`}>{feature.title}</span>
                </h3>
                <p className="text-sm leading-7 text-ink-2">{feature.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 py-20">
        <h2 className="rule-under text-3xl font-extrabold text-ink">Available now</h2>
        <p className="mt-4 max-w-xl text-sm leading-relaxed text-ink-2">
          One subject done properly beats forty done badly — a wrongly tagged question
          wastes the revision time it was supposed to save. Subjects are published as
          their question banks pass review.
        </p>

        <div className="stagger mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {published.map((subject) => (
            <Link
              key={subject.slug}
              href={`/subjects/${subject.slug}`}
              className="lift group rounded-2xl border-2 border-edge bg-surface p-5 shadow-[4px_4px_0_var(--pop)]"
            >
              <span className="hl hl-blue font-mono text-xs uppercase tracking-widest">
                {subject.syllabusCode}
              </span>
              <p className="mt-3 text-2xl font-bold text-ink">{subject.title}</p>
              <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-bold text-accent">
                Open
                <span
                  aria-hidden
                  className="transition-transform duration-200 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:translate-x-1"
                >
                  →
                </span>
              </span>
            </Link>
          ))}

          {inProgress.map((subject) => (
            <div
              key={subject.slug}
              className="rounded-2xl border-2 border-dashed border-line-strong bg-surface/70 p-5 text-ink-3"
            >
              <p className="font-mono text-xs uppercase tracking-widest">
                {subject.syllabusCode}
              </p>
              <p className="mt-3 text-2xl font-bold">{subject.title}</p>
              <span className="mt-4 inline-block text-sm">In progress</span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

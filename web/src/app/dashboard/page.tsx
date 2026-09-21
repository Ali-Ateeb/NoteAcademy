import type { Metadata } from "next";

import { DashboardOverview } from "@/components/DashboardOverview";
import { getSubjects } from "@/lib/data/catalog";

export const metadata: Metadata = {
  title: "Dashboard",
  description: "Accuracy per topic and time per question across your attempts.",
};

export default async function DashboardPage() {
  const subjects = (await getSubjects()).filter((s) => s.isPublished);

  const summaries = subjects.map((subject) => ({
    slug: subject.slug,
    title: subject.title,
    syllabusCode: subject.syllabusCode,
  }));

  return (
    <div className="mx-auto max-w-4xl px-5 py-12">
      <h1 className="rule-under font-serif text-4xl font-extrabold tracking-tight text-ink">Your dashboard</h1>
      <p className="mt-3 max-w-xl leading-relaxed text-ink-2">
        Pick a subject to see accuracy per topic, worst first — revision goes
        where it is needed, not where it is comfortable.
      </p>
      <DashboardOverview subjects={summaries} />
    </div>
  );
}

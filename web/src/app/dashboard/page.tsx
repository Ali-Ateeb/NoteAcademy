import type { Metadata } from "next";

import { Dashboard } from "@/components/Dashboard";
import { getMcqQuestions, getTopics } from "@/lib/data/catalog";

export const metadata: Metadata = {
  title: "Dashboard",
  description: "Accuracy per topic and time per question across your attempts.",
};

export default async function DashboardPage() {
  const [topics, questions] = await Promise.all([
    getTopics("physics-5054"),
    getMcqQuestions("physics-5054-2019-may-june-p12"),
  ]);

  return (
    <div className="mx-auto max-w-4xl px-5 py-12">
      <h1 className="font-serif text-4xl tracking-tight text-ink">Your weak areas</h1>
      <p className="mt-3 max-w-xl leading-relaxed text-ink-2">
        Accuracy per topic, worst first. Revision goes where it is needed, not
        where it is comfortable.
      </p>
      <Dashboard
        topics={topics}
        questionTopics={questions.map((q) => ({ id: q.id, topicCodes: q.topicCodes }))}
      />
    </div>
  );
}

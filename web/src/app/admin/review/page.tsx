import type { Metadata } from "next";

import { ReviewQueue } from "@/components/ReviewQueue";
import {
  getReviewQueue,
  getReviewTopicOptions,
  isBackedByDatabase,
} from "@/lib/data/catalog";

/** Rendered per request, not at build time. The queue changes as papers are
 *  ingested, and its crops are signed URLs that expire — a prerendered page
 *  would show a stale queue full of dead image links. */
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Review queue",
  description: "Approve or reject questions the ingestion pipeline was unsure about.",
  robots: { index: false, follow: false },
};

export default async function ReviewPage() {
  const [items, topicOptions] = await Promise.all([
    getReviewQueue(),
    getReviewTopicOptions(),
  ]);

  return (
    <div className="mx-auto max-w-5xl px-5 py-12">
      <h1 className="font-serif text-4xl tracking-tight text-ink">Review queue</h1>
      <p className="mt-3 max-w-2xl leading-relaxed text-ink-2">
        Questions the pipeline extracted but would not publish on its own. Nothing
        here is visible to students until it is approved — which is what keeps a
        wrong topic tag from quietly wasting someone&apos;s revision time.
      </p>
      {/* With no database there is nothing to write to, and the queue is a
          scaffold running on fixtures — decisions stay in the browser and that
          is the whole intent. With one, a decision that does not reach it has
          not happened, and the difference has to be visible. */}
      <ReviewQueue
        items={items}
        topicOptions={topicOptions}
        persist={isBackedByDatabase()}
      />
    </div>
  );
}

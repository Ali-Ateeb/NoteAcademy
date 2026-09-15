import type { Metadata } from "next";
import { cookies } from "next/headers";

import { AdminGate } from "@/components/AdminGate";
import { ReviewQueue } from "@/components/ReviewQueue";
import {
  getDecidedQuestions,
  getReviewQueue,
  getReviewTopicOptions,
  getSubjects,
  isBackedByDatabase,
} from "@/lib/data/catalog";
import { ADMIN_COOKIE, reviewTokenConfigured, tokenMatches } from "@/lib/reviewAuth";

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
  // With no database there is nothing unapproved to protect — this is the
  // fixtures scaffold the README promises runs with no keys at all — so the
  // gate below applies only once there is a real bank behind the page.
  if (isBackedByDatabase()) {
    if (!reviewTokenConfigured()) {
      return (
        <div className="mx-auto max-w-sm px-5 py-16">
          <h1 className="font-serif text-3xl tracking-tight text-ink">Review queue</h1>
          <p className="mt-3 text-sm leading-relaxed text-incorrect">
            REVIEW_TOKEN is not set. The queue holds unapproved, unpublished
            questions, so it refuses to render at all rather than serve them
            with nothing protecting them.
          </p>
        </div>
      );
    }

    const cookieStore = await cookies();
    const authorized = tokenMatches(cookieStore.get(ADMIN_COOKIE)?.value);
    // The gate on purpose: nothing below this line runs — no crop, mark
    // scheme or correct option is even fetched — until the request itself
    // carries proof it is allowed to see them.
    if (!authorized) return <AdminGate />;
  }

  const [items, subjects, decided] = await Promise.all([
    getReviewQueue(),
    getSubjects(),
    getDecidedQuestions(),
  ]);

  // The queue mixes every subject in one list, but getReviewTopicOptions
  // defaults to physics-5054 — passing one flat list here used to put
  // Physics topics in a Biology or Chemistry question's "Change to" dropdown,
  // the one control a reviewer has for fixing a wrong tag. Grouped by
  // subject instead, so the dropdown can only ever offer topics that
  // actually belong to the question on screen.
  const topicGroups = await Promise.all(
    subjects.map(async (subject) => ({
      subjectSlug: subject.slug,
      subjectTitle: subject.title,
      topics: await getReviewTopicOptions(subject.slug),
    })),
  );

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
        topicGroups={topicGroups}
        decided={decided}
        persist={isBackedByDatabase()}
        // Whether the *server* is configured to accept writes at all. Without
        // this the page cannot tell "you have not unlocked this browser yet"
        // from "saving is switched off", and says the wrong one half the time.
        savingConfigured={Boolean(process.env.REVIEW_TOKEN)}
      />
    </div>
  );
}

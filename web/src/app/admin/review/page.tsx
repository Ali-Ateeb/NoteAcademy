import type { Metadata } from "next";
import Link from "next/link";

import { ReviewQueue } from "@/components/ReviewQueue";
import {
  getDecidedQuestions,
  getReviewQueue,
  getReviewTopicOptions,
  getSubjects,
  isBackedByDatabase,
} from "@/lib/data/catalog";
import { reviewerStatus } from "@/lib/reviewerAuth";

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
  let reviewerEmail: string | null = null;
  if (isBackedByDatabase()) {
    const status = await reviewerStatus();
    // The gate on purpose: nothing below this line runs — no crop, mark
    // scheme or correct option is even fetched — until the request itself
    // carries proof (a signed-in, is_reviewer account) that it may see them.
    if (status.kind !== "reviewer") {
      return <NotAReviewer status={status} />;
    }
    reviewerEmail = status.reviewer.email;
  }

  const [firstPage, subjects, decided] = await Promise.all([
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
      <h1 className="rule-under font-serif text-4xl font-extrabold tracking-tight text-ink">Review queue</h1>
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
        initialItems={firstPage.items}
        initialTotal={firstPage.total}
        subjects={subjects.map((s) => ({ slug: s.slug, title: s.title }))}
        topicGroups={topicGroups}
        decided={decided}
        persist={isBackedByDatabase()}
        reviewerEmail={reviewerEmail}
      />
    </div>
  );
}

/** What a visitor without review access sees — distinguishing "sign in
 *  first" from "your account is not a reviewer" rather than one generic
 *  refusal, since the fix for each is different (and the second one is not
 *  a fix this page can offer at all: is_reviewer is set from outside the
 *  app, by whoever operates the database). */
function NotAReviewer({ status }: { status: { kind: "signed-out" | "not-a-reviewer"; email?: string | null } }) {
  return (
    <div className="mx-4 my-12 max-w-sm min-[420px]:mx-auto rounded-[2rem] border-2 border-edge bg-surface px-7 py-9 shadow-[4px_4px_0_var(--pop)]">
      <h1 className="font-serif text-3xl font-extrabold tracking-tight text-ink">Review queue</h1>
      <p className="mt-3 text-sm leading-relaxed text-ink-2">
        This page holds unapproved questions — crops, mark schemes, correct
        options — none of which is meant to be public yet.
      </p>
      {status.kind === "signed-out" ? (
        <>
          <p className="mt-4 text-sm leading-relaxed text-ink-2">
            Sign in with a reviewer account to continue.
          </p>
          <Link
            href="/login?next=/admin/review"
            className="pill pill-solid mt-4 px-5 py-2.5 text-sm"
          >
            Sign in
          </Link>
        </>
      ) : (
        <p className="mt-4 text-sm leading-relaxed text-incorrect">
          {status.email ?? "This account"} is signed in but is not a reviewer.
          Access is granted from the database (
          <span className="font-mono">profiles.is_reviewer</span>), not from
          this page.
        </p>
      )}
    </div>
  );
}

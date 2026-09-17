/**
 * The pool of questions behind the weak-topic revision queue
 * (`ReviseWeakTopics.tsx`): every approved MCQ tagged to any of a given set
 * of topics, in one request.
 *
 * No auth gate: which topics are "weak" is computed client-side from a
 * student's own attempts (local or `current_answers`, either signed out or
 * in), so this route only ever needs to answer "what questions exist on
 * these topics" — the same public content `/topics/[subject]/[topic]`
 * already serves to a signed-out visitor. Nothing here is scoped to a user.
 */

import { NextResponse } from "next/server";

import { getQuestionsByTopics } from "@/lib/data/catalog";

// A revision session is one sitting, not a full-subject re-tag — this is
// the same ceiling ReviseWeakTopics.tsx caps the weak-topic list at, kept
// here too so the route can't be asked to assemble an unbounded query.
const MAX_TOPICS = 5;

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const subject = searchParams.get("subject");
  const topicsParam = searchParams.get("topics");

  if (!subject || !topicsParam) {
    return NextResponse.json(
      { error: "subject and topics query params are required." },
      { status: 400 },
    );
  }

  const topics = topicsParam
    .split(",")
    .map((code) => code.trim())
    .filter(Boolean)
    .slice(0, MAX_TOPICS);

  if (topics.length === 0) {
    return NextResponse.json({ error: "topics must name at least one topic code." }, { status: 400 });
  }

  try {
    const questions = await getQuestionsByTopics(subject, topics);
    return NextResponse.json({ questions });
  } catch (error) {
    return NextResponse.json({ error: (error as Error).message }, { status: 500 });
  }
}

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

// What a syllabus code and a subject slug actually look like ("1.5.2",
// "physics-5054"). Checked before the query is built: the codes go into a
// Postgres array literal, and one holding a quote, brace or backslash used to
// reach the database as a malformed literal -- a 500 that also handed the
// caller the raw Postgres error. Anything that is not one of these cannot name
// a topic, so it is refused rather than passed on to fail.
const TOPIC_CODE = /^[0-9A-Za-z][0-9A-Za-z.-]{0,15}$/;
const SUBJECT_SLUG = /^[a-z0-9][a-z0-9-]{0,63}$/;

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
  if (!SUBJECT_SLUG.test(subject) || !topics.every((code) => TOPIC_CODE.test(code))) {
    return NextResponse.json(
      { error: "subject or topics is not a valid slug or topic code." },
      { status: 400 },
    );
  }

  try {
    const questions = await getQuestionsByTopics(subject, topics);
    return NextResponse.json({ questions });
  } catch (error) {
    return NextResponse.json({ error: (error as Error).message }, { status: 500 });
  }
}

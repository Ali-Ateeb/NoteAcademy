/**
 * A full worked solution for a question, generated once and cached forever.
 *
 * question_solutions (0025) is the point of this route: an LLM call is the
 * most expensive thing this app does, and the syllabus content of a question
 * does not change between the first student who asks and the thousandth, so
 * the second request for the same question is a cache read, not a second
 * call. Gated to signed-in students — an anonymous visitor cannot be metered
 * against usage_counters, and an unmetered endpoint calling a paid API is a
 * bill with no ceiling.
 *
 * The free-tier daily cap uses consume_quota (0006_billing.sql), the same
 * function a real subscription tier will check against once payments land —
 * this route does not know or care which plan the caller is on, only that
 * something already decided the day's limit for them.
 */

import Anthropic from "@anthropic-ai/sdk";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";

import { serverSupabase } from "@/lib/supabase/serverClient";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const SOLVER_MODEL = process.env.SOLVER_MODEL || "claude-opus-5";

// Free-tier ceiling until a real plan_tier lookup replaces the flat number.
// Named here rather than buried in the call below so it is the one line to
// change when pricing exists.
const FREE_DAILY_LIMIT = 5;

function serviceClient(): SupabaseClient | null {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY ?? "";
  if (!SUPABASE_URL || !key) return null;
  return createClient(SUPABASE_URL, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

interface QuestionRow {
  id: string;
  display_label: string;
  question_type: string;
  question_text: string | null;
  mark_scheme_text: string | null;
  examiner_comment: string | null;
  correct_option: string | null;
  extraction_status: string;
}

interface OptionRow {
  option: string;
  content: string;
}

function buildPrompt(question: QuestionRow, options: OptionRow[]): string {
  const parts = [
    `Question ${question.display_label} (${question.question_type}):`,
    question.question_text?.trim() || "[No question text was extracted — work from the mark scheme below.]",
  ];

  if (options.length) {
    parts.push(
      "Options:",
      options.map((o) => `${o.option}. ${o.content}`).join("\n"),
    );
  }
  if (question.correct_option) {
    parts.push(`Correct option: ${question.correct_option}`);
  }
  if (question.mark_scheme_text) {
    parts.push(`Mark scheme:\n${question.mark_scheme_text}`);
  }
  if (question.examiner_comment) {
    parts.push(`Examiner report note:\n${question.examiner_comment}`);
  }

  parts.push(
    "\nWrite a full worked solution for a student revising this topic: show every "
      + "step of the method, state the reasoning behind each step, and end with the "
      + "final answer. Use the mark scheme to make sure the method and the answer are "
      + "both correct, but explain it in prose rather than reproducing mark-scheme "
      + "shorthand. Do not mention that a mark scheme was supplied.",
  );

  return parts.join("\n\n");
}

export async function POST(request: Request) {
  const anthropicKey = process.env.ANTHROPIC_API_KEY ?? "";
  const service = serviceClient();
  if (!anthropicKey || !service) {
    return NextResponse.json(
      { error: "The solver isn't configured for this app instance." },
      { status: 503 },
    );
  }

  const auth = await serverSupabase();
  const {
    data: { user },
  } = (await auth?.auth.getUser()) ?? { data: { user: null } };
  if (!user) {
    return NextResponse.json({ error: "Sign in to use the solver." }, { status: 401 });
  }

  const body = await request.json().catch(() => null);
  const questionId = typeof body?.questionId === "string" ? body.questionId : null;
  if (!questionId) {
    return NextResponse.json({ error: "questionId is required." }, { status: 400 });
  }

  const { data: cached } = await service
    .from("question_solutions")
    .select("solution")
    .eq("question_id", questionId)
    .maybeSingle();
  if (cached) {
    return NextResponse.json({ solution: cached.solution as string, cached: true });
  }

  const { data: question } = await service
    .from("questions")
    .select(
      "id,display_label,question_type,question_text,mark_scheme_text,examiner_comment,correct_option,extraction_status",
    )
    .eq("id", questionId)
    .maybeSingle<QuestionRow>();
  // Same gate as everything else served to students: unapproved leaks nothing.
  if (!question || question.extraction_status !== "approved") {
    return NextResponse.json({ error: "Question not found." }, { status: 404 });
  }

  const { data: withinQuota } = await service.rpc("consume_quota", {
    p_user_id: user.id,
    p_metric: "ai_query",
    p_limit: FREE_DAILY_LIMIT,
  });
  if (!withinQuota) {
    return NextResponse.json(
      {
        error: `You've used today's ${FREE_DAILY_LIMIT} free solver requests. Try again tomorrow.`,
      },
      { status: 429 },
    );
  }

  let options: OptionRow[] = [];
  if (question.question_type === "mcq") {
    const { data } = await service
      .from("question_options")
      .select("option,content")
      .eq("question_id", questionId)
      .order("option");
    options = (data as OptionRow[] | null) ?? [];
  }

  const anthropic = new Anthropic({ apiKey: anthropicKey });
  const response = await anthropic.messages.create({
    model: SOLVER_MODEL,
    max_tokens: 2000,
    messages: [{ role: "user", content: buildPrompt(question, options) }],
  });

  const solution = response.content
    .filter((block): block is Anthropic.TextBlock => block.type === "text")
    .map((block) => block.text)
    .join("\n")
    .trim();

  if (!solution) {
    return NextResponse.json({ error: "The solver returned nothing usable." }, { status: 502 });
  }

  // Best-effort: a caching failure should not cost the student the answer
  // they just paid a quota unit for.
  await service
    .from("question_solutions")
    .upsert({ question_id: questionId, solution, model: SOLVER_MODEL });

  return NextResponse.json({ solution, cached: false });
}

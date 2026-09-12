import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import { isAuthConfigured, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./env";

/**
 * The session-aware client for server components and route handlers: reads
 * the session out of the request's cookies rather than a bundled key, so
 * `select`s made with it run as the signed-in user and pick up their RLS
 * rows (current_answers, practice_sessions, …) — not the anonymous role
 * catalog.ts's server-side client always uses.
 *
 * Never cached across requests, unlike the browser client: it is built from
 * *this* request's cookies, and every request gets its own.
 */
export async function serverSupabase() {
  if (!isAuthConfigured()) return null;
  const cookieStore = await cookies();

  return createServerClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Called from a Server Component, which cannot set cookies. The
          // middleware below refreshes the session on every request instead,
          // so a read-only call site here is harmless rather than silently
          // stale.
        }
      },
    },
  });
}

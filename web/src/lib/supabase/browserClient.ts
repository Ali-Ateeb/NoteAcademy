"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

import { isAuthConfigured, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./env";

/**
 * The session-aware client for client components: cookie-backed, so the
 * session survives a reload and is visible to the server client reading the
 * same cookies in a server component or route handler.
 *
 * Cached per module rather than per call — one client, reused, the same
 * reasoning catalog.ts's `db()` already applies to the anonymous client.
 * Returns null when unconfigured so a signed-out, no-backend dev clone keeps
 * working exactly as it does today: auth is additive, never required.
 */
let cached: SupabaseClient | null | undefined;

export function browserSupabase(): SupabaseClient | null {
  if (cached !== undefined) return cached;
  cached = isAuthConfigured()
    ? createBrowserClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
    : null;
  return cached;
}

/**
 * Shared Supabase URL/key reading for the auth-aware clients.
 *
 * The same two env vars catalog.ts already reads — never NEXT_PUBLIC_SUPABASE_ANON_KEY,
 * which is what web/.env.example used to call it before this file's sibling
 * clients were added; the code has read PUBLISHABLE_KEY since catalog.ts was
 * written, and .env.example was fixed to match rather than the other way
 * round.
 */
export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_PUBLISHABLE_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "";

export function isAuthConfigured(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_PUBLISHABLE_KEY);
}

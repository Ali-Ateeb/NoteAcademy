import { NextResponse } from "next/server";

import { serverSupabase } from "@/lib/supabase/serverClient";

/**
 * Where every Supabase auth email link points: signup confirmation and
 * password-reset links both carry a `code` that has to be exchanged for a
 * session before the destination page can do anything session-dependent —
 * reset-password's `updateUser({ password })` needs a live session to know
 * whose password it is changing.
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/dashboard";

  if (code) {
    const supabase = await serverSupabase();
    if (supabase) {
      const { error } = await supabase.auth.exchangeCodeForSession(code);
      if (!error) return NextResponse.redirect(`${origin}${next}`);
    }
  }

  return NextResponse.redirect(`${origin}/login?error=auth-callback-failed`);
}

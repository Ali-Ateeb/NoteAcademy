import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

import { isAuthConfigured, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "@/lib/supabase/env";

/**
 * Refreshes the Supabase session cookie on every request.
 *
 * Access tokens expire; without this, a server component's `serverSupabase()`
 * would start reading an expired session partway through a visit and the
 * student would appear signed out mid-session for no reason they did
 * anything to cause. This is the standard Supabase SSR pattern for Next.js:
 * middleware is the one place with both read and write access to request
 * cookies, so it is the only place a refreshed token can actually be
 * persisted back to the browser.
 */
export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request });

  if (!isAuthConfigured()) return response;

  const supabase = createServerClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  // Reading the user is what actually triggers a refresh when the access
  // token has expired — a plain getSession() would just return the stale one.
  await supabase.auth.getUser();

  return response;
}

export const config = {
  matcher: [
    // Every route except static assets and Next's own image optimiser —
    // running this against a paper's crop image would spend a database round
    // trip refreshing a session nobody asked for on a request that carries no
    // cookies to refresh.
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};

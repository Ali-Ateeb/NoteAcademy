"use client";

import type { SupabaseClient, User } from "@supabase/supabase-js";
import { createContext, useContext, useEffect, useRef, useState } from "react";

import { migrateLocalAttempts } from "@/lib/attempts";
import { browserSupabase } from "@/lib/supabase/browserClient";

interface AuthState {
  /** null once resolved and signed out; undefined while the initial session
   *  check is still in flight, which is what lets a page avoid flashing
   *  "signed out" UI for the instant before Supabase reports otherwise. */
  user: User | null | undefined;
  supabase: SupabaseClient | null;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  supabase: null,
  signOut: async () => {},
});

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

/**
 * Wraps the app so any client component can ask "who is signed in" without
 * re-deriving it. With no Supabase configuration this is inert — `supabase`
 * stays null and `user` stays null forever, which is exactly the signed-out,
 * local-storage-only behaviour the app already has.
 *
 * The one thing this does beyond exposing state: the moment a SIGNED_IN event
 * arrives, it pushes whatever attempts this browser recorded while signed out
 * into the student's account. That is the entire migration path — one line,
 * fired from the one place in the app that reliably knows a sign-in just
 * happened, rather than every page that might be the first one a student
 * lands on after logging in having to remember to call it.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const supabase = useRef(browserSupabase()).current;
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    if (!supabase) {
      setUser(null);
      return;
    }

    supabase.auth.getUser().then(({ data }) => setUser(data.user));

    const { data: subscription } = supabase.auth.onAuthStateChange((event, session) => {
      setUser(session?.user ?? null);
      if (event === "SIGNED_IN" && session?.user) {
        void migrateLocalAttempts(supabase, session.user.id);
      }
    });

    return () => subscription.subscription.unsubscribe();
  }, [supabase]);

  async function signOut() {
    await supabase?.auth.signOut();
  }

  return (
    <AuthContext.Provider value={{ user, supabase, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

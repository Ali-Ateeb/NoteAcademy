import type { Metadata } from "next";
import Link from "next/link";

import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "NoteAcademy — Cambridge past papers, by topic",
    template: "%s · NoteAcademy",
  },
  description:
    "Every CAIE past paper, split into individual questions, tagged to your syllabus, with the mark scheme and examiner report attached.",
};

function Header() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-paper/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-5">
        <Link href="/" className="font-semibold tracking-tight text-ink">
          Note<span className="text-accent">Academy</span>
        </Link>
        <nav className="hidden gap-5 text-sm text-ink-2 sm:flex">
          <Link href="/subjects" className="transition-colors hover:text-ink">
            Subjects
          </Link>
          <Link href="/dashboard" className="transition-colors hover:text-ink">
            Dashboard
          </Link>
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <ThemeToggle />
          <Link
            href="/subjects"
            className="rounded-lg bg-accent px-3.5 py-1.5 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90"
          >
            Start revising
          </Link>
        </div>
      </div>
    </header>
  );
}

function Footer() {
  return (
    <footer className="mt-24 border-t border-line py-10">
      <div className="mx-auto max-w-6xl px-5 text-sm text-ink-3">
        <p>
          NoteAcademy is an independent revision tool and is not affiliated with,
          endorsed by, or connected to Cambridge Assessment International
          Education.
        </p>
      </div>
    </footer>
  );
}

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // Both elements are written to before React hydrates, for different
    // reasons: <html> by the theme script below, <body> by whatever extensions
    // the reader happens to run — Grammarly adds data-gr-ext-installed and
    // data-new-gr-c-s-check-loaded, password managers add their own.
    //
    // suppressHydrationWarning reaches exactly one level: the element's own
    // attributes and text, never its children. So this silences the noise
    // without hiding a real mismatch inside the app — which is the point.
    // Unsuppressed, every page load logs a mismatch nobody can act on, and the
    // one that matters arrives looking identical to the ones that don't.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applied before first paint so the page never flashes the wrong
            theme. Inline and synchronous on purpose. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("na-theme");if(t)document.documentElement.setAttribute("data-theme",t)}catch(e){}})()`,
          }}
        />
      </head>
      <body suppressHydrationWarning>
        <Header />
        <main>{children}</main>
        <Footer />
      </body>
    </html>
  );
}

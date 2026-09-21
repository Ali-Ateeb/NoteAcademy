import type { Metadata } from "next";
import { IBM_Plex_Mono, Raleway } from "next/font/google";
import Link from "next/link";

import { AccountMenu } from "@/components/AccountMenu";
import { AuthProvider } from "@/components/AuthProvider";
import { LogoLong } from "@/components/Brand";
import { NavLinks } from "@/components/NavLinks";
import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

/* Two voices. Raleway is the Note Academy typeface — the same family the
 * original site sets its headings and buttons in — and it is a variable font,
 * so one file covers the 450 body weight and the 700–800 display weights.
 * Plex Mono is kept for what the exam board itself sets in monospace: paper
 * codes, question numbers, marks, the timer. Both are self-hosted at build
 * time by next/font, so there is no render-blocking request to Google and no
 * flash of fallback text. */
const raleway = Raleway({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-raleway",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-plex-mono",
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: {
    default: "Note Academy — Cambridge past papers, by topic",
    template: "%s · Note Academy",
  },
  description:
    "Every CAIE past paper, split into individual questions, tagged to your syllabus, with the official mark scheme attached.",
};

function Header() {
  return (
    <header className="sticky top-0 z-40 px-3 pt-3">
      {/* The toolbar from the Note Academy site, as one floating bar: logo on
          the left, the pill buttons in the middle, the account on the right. */}
      <div className="mx-auto flex max-w-6xl items-center gap-2 rounded-full border border-line bg-surface/90 py-2 pr-2 pl-3 sm:gap-3 sm:pl-4 shadow-[0_8px_30px_-18px_rgb(17_19_24/0.35)] backdrop-blur-md">
        <Link href="/" aria-label="Note Academy home" className="shrink-0 transition-transform duration-200 hover:scale-[1.03]">
          <LogoLong className="h-6 sm:h-8" priority />
        </Link>
        <NavLinks className="ml-4 hidden sm:flex" />
        <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
          <AccountMenu />
          <ThemeToggle />
          <Link href="/subjects" className="pill pill-solid px-4 py-1.5 text-sm whitespace-nowrap">
            <span className="sm:hidden">Start</span>
            <span className="hidden sm:inline">Start revising</span>
          </Link>
        </div>
      </div>
    </header>
  );
}

function Footer() {
  return (
    <footer className="mt-24 border-t border-line bg-surface py-10">
      <div className="mx-auto flex max-w-6xl flex-col gap-5 px-5 sm:flex-row sm:items-center sm:gap-8">
        <div className="shrink-0">
          <LogoLong className="h-8" />
        </div>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-3">
          Note Academy is an independent revision tool and is not affiliated with,
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
    <html
      lang="en"
      className={`${raleway.variable} ${plexMono.variable}`}
      suppressHydrationWarning
    >
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
        <AuthProvider>
          <Header />
          <main>{children}</main>
          <Footer />
        </AuthProvider>
      </body>
    </html>
  );
}

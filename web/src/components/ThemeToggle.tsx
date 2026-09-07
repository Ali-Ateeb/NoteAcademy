"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

/**
 * Writes an explicit choice to the document root and remembers it. With nothing
 * stored the page follows the system setting, which is why the root attribute is
 * absent rather than set to a default.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    const stored = (() => {
      try {
        return localStorage.getItem("na-theme") as Theme | null;
      } catch {
        return null;
      }
    })();

    if (stored) {
      setTheme(stored);
      return;
    }
    setTheme(
      window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
    );
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("na-theme", next);
    } catch {
      // Private browsing, or site data blocked. The toggle still works for
      // this page view; it just will not be remembered.
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle colour theme"
      className="rounded-lg border border-line px-2 py-1.5 text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}

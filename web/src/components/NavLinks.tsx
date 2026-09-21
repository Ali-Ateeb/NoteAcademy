"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type Href = "/" | "/subjects" | "/dashboard";

/* A section owns every route beneath the pages that lead to it: a student on a
 * paper or a topic is still "in Subjects", and the toolbar should say so. */
const ITEMS: { href: Href; label: string; owns: string[] }[] = [
  { href: "/", label: "Home", owns: [] },
  { href: "/subjects", label: "Subjects", owns: ["/subjects", "/papers", "/topics", "/practice"] },
  { href: "/dashboard", label: "Dashboard", owns: ["/dashboard"] },
];

export function NavLinks({ className = "" }: { className?: string }) {
  const pathname = usePathname();

  return (
    <nav aria-label="Main" className={`gap-1.5 text-sm ${className}`}>
      {ITEMS.map((item) => {
        const active =
          item.href === "/"
            ? pathname === "/"
            : item.owns.some((prefix) => pathname.startsWith(prefix));
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className="pill px-4 py-1.5"
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { PaperDocType } from "@/lib/data/types";
import { DOC_LABELS } from "@/lib/data/types";

interface Props {
  paperSlug: string;
  available: PaperDocType[];
}

/**
 * Question paper on the left, mark scheme or examiner report on the right.
 *
 * The pane is a placeholder until object storage is wired up — real documents
 * arrive as short-lived signed URLs rendered with PDF.js, never as public bucket
 * links. What is real here is the interaction model, which is the part students
 * feel: synchronised scrolling, a draggable split, and keyboard shortcuts.
 */
export function SplitViewer({ paperSlug, available }: Props) {
  const secondaries = available.filter((d) => d !== "qp");
  const [secondary, setSecondary] = useState<PaperDocType>(secondaries[0] ?? "ms");
  const [split, setSplit] = useState(50);
  const [synced, setSynced] = useState(true);
  const [dragging, setDragging] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  // Guards against the scroll handlers driving each other into a feedback loop.
  const syncing = useRef(false);

  const onDrag = useCallback((event: PointerEvent) => {
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const pct = ((event.clientX - rect.left) / rect.width) * 100;
    setSplit(Math.min(80, Math.max(20, pct)));
  }, []);

  useEffect(() => {
    if (!dragging) return;
    const stop = () => setDragging(false);
    window.addEventListener("pointermove", onDrag);
    window.addEventListener("pointerup", stop);
    // Stops the drag selecting text across both panes.
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("pointermove", onDrag);
      window.removeEventListener("pointerup", stop);
      document.body.style.userSelect = "";
    };
  }, [dragging, onDrag]);

  function mirror(from: HTMLDivElement | null, to: HTMLDivElement | null) {
    if (!synced || !from || !to || syncing.current) return;
    syncing.current = true;
    const range = from.scrollHeight - from.clientHeight;
    const ratio = range > 0 ? from.scrollTop / range : 0;
    to.scrollTop = ratio * (to.scrollHeight - to.clientHeight);
    requestAnimationFrame(() => {
      syncing.current = false;
    });
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA)$/.test(target.tagName)) return;

      if (event.key === "m" && secondaries.includes("ms")) setSecondary("ms");
      if (event.key === "e" && secondaries.includes("er")) setSecondary("er");
      if (event.key === "s") setSynced((value) => !value);
      if (event.key === "0") setSplit(50);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [secondaries]);

  return (
    <div className="rounded-2xl border border-line bg-surface shadow-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <div className="flex gap-1">
          {secondaries.map((doc) => (
            <button
              key={doc}
              type="button"
              onClick={() => setSecondary(doc)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                secondary === doc
                  ? "bg-accent text-accent-ink"
                  : "text-ink-2 hover:bg-surface-2"
              }`}
            >
              {DOC_LABELS[doc]}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-3 text-xs text-ink-3">
          <label className="flex cursor-pointer items-center gap-1.5">
            <input
              type="checkbox"
              checked={synced}
              onChange={(event) => setSynced(event.target.checked)}
              className="accent-[var(--accent)]"
            />
            Sync scroll
          </label>
          <span className="hidden sm:inline">
            <kbd className="rounded border border-line px-1">M</kbd> mark scheme ·{" "}
            <kbd className="rounded border border-line px-1">E</kbd> report ·{" "}
            <kbd className="rounded border border-line px-1">0</kbd> reset
          </span>
        </div>
      </div>

      <div
        ref={containerRef}
        className="flex h-[70vh] min-h-[420px] flex-col md:flex-row"
      >
        <div
          ref={leftRef}
          onScroll={() => mirror(leftRef.current, rightRef.current)}
          className="scroll-x flex-1 overflow-y-auto p-5 md:flex-none"
          style={{ flexBasis: `${split}%` }}
        >
          <DocumentPane label="Question Paper" paperSlug={paperSlug} docType="qp" />
        </div>

        <div
          role="separator"
          aria-label="Resize panes"
          aria-orientation="vertical"
          onPointerDown={() => setDragging(true)}
          className={`hidden w-1 shrink-0 cursor-col-resize bg-line transition-colors hover:bg-accent md:block ${
            dragging ? "bg-accent" : ""
          }`}
        />

        <div
          ref={rightRef}
          onScroll={() => mirror(rightRef.current, leftRef.current)}
          className="scroll-x flex-1 overflow-y-auto border-t border-line p-5 md:border-l md:border-t-0"
        >
          <DocumentPane
            label={DOC_LABELS[secondary]}
            paperSlug={paperSlug}
            docType={secondary}
          />
        </div>
      </div>
    </div>
  );
}

function DocumentPane({
  label,
  paperSlug,
  docType,
}: {
  label: string;
  paperSlug: string;
  docType: PaperDocType;
}) {
  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-ink">{label}</h3>
        <span className="font-mono text-[10px] uppercase text-ink-3">{docType}</span>
      </div>
      <div className="flex min-h-[300px] items-center justify-center rounded-xl border border-dashed border-line bg-surface-2 p-8 text-center">
        <div>
          <p className="text-sm text-ink-2">
            {label} for <span className="font-mono text-xs">{paperSlug}</span>
          </p>
          <p className="mt-2 max-w-xs text-xs leading-relaxed text-ink-3">
            Documents render here via PDF.js once object storage is connected.
            They are served as short-lived signed URLs, never as public links.
          </p>
        </div>
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { PaperDocType } from "@/lib/data/types";
import { DOC_LABELS } from "@/lib/data/types";
import type { PDFDocumentProxy } from "pdfjs-dist";

interface Props {
  paperSlug: string;
  available: PaperDocType[];
}

/**
 * Question paper on the left, mark scheme or examiner report on the right.
 *
 * Each pane resolves its own short-lived signed URL from `/api/paper-doc`
 * and renders it with PDF.js — never a public bucket link, and never baked
 * into the page (see that route's own note on why). The interaction model
 * around the documents is what students actually feel day to day:
 * synchronised scrolling, a draggable split, and keyboard shortcuts.
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
    <div className="overflow-hidden rounded-2xl border-2 border-edge bg-surface shadow-[4px_4px_0_var(--pop)]">
      <div className="flex flex-wrap items-center gap-2 border-b-2 border-line bg-surface-2 px-3 py-2">
        <div className="flex gap-1">
          {secondaries.map((doc) => (
            <button
              key={doc}
              type="button"
              onClick={() => setSecondary(doc)}
              className={`rounded-full px-3.5 py-1 text-xs font-bold transition-all duration-150 ${
                secondary === doc
                  ? "bg-accent text-accent-ink"
                  : "text-ink-2 hover:bg-hl-blue hover:text-ink"
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

type PaneState =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error" }
  | { kind: "ready"; url: string };

function DocumentPane({
  label,
  paperSlug,
  docType,
}: {
  label: string;
  paperSlug: string;
  docType: PaperDocType;
}) {
  const [state, setState] = useState<PaneState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetch(`/api/paper-doc/${encodeURIComponent(paperSlug)}/${docType}`)
      .then(async (res) => {
        if (cancelled) return;
        if (res.status === 404) {
          setState({ kind: "missing" });
          return;
        }
        if (!res.ok) {
          setState({ kind: "error" });
          return;
        }
        const data = (await res.json()) as { url: string };
        setState({ kind: "ready", url: data.url });
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "error" });
      });

    return () => {
      cancelled = true;
    };
  }, [paperSlug, docType]);

  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-sm">
          <span className="hl hl-blue font-bold">{label}</span>
        </h3>
        <span className="font-mono text-[10px] uppercase text-ink-3">{docType}</span>
      </div>

      {state.kind === "ready" ? (
        <PdfPages url={state.url} />
      ) : (
        <div className="flex min-h-[300px] items-center justify-center rounded-xl border-2 border-dashed border-note-line bg-note p-8 text-center">
          <p className="text-sm text-ink-2">
            {state.kind === "loading" && `Loading ${label.toLowerCase()}…`}
            {state.kind === "missing" &&
              `${label} isn't available for this paper yet.`}
            {state.kind === "error" && `Couldn't load ${label.toLowerCase()}.`}
          </p>
        </div>
      )}
    </div>
  );
}

/** Renders every page of a signed-URL PDF as a stacked column of canvases —
 *  continuous scroll, not pagination, so the split viewer's existing
 *  scroll-sync (a plain scrollTop ratio) needs no extra state for "which
 *  page". PDF.js itself is loaded lazily inside the effect: it touches
 *  `document`/canvas at import time, which does not exist during this
 *  client component's server-side render pass. */
function PdfPages({ url }: { url: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let loadingTask: { destroy(): Promise<void> } | null = null;

    async function render() {
      const pdfjsLib = await import("pdfjs-dist");
      pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
        "pdfjs-dist/build/pdf.worker.min.mjs",
        import.meta.url,
      ).toString();

      const task = pdfjsLib.getDocument({ url });
      loadingTask = task;
      const loaded: PDFDocumentProxy = await task.promise;
      if (cancelled) return;

      const container = containerRef.current;
      if (!container) return;
      container.replaceChildren();

      const targetWidth = container.clientWidth || 600;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);

      for (let n = 1; n <= loaded.numPages; n++) {
        if (cancelled) return;
        const page = await loaded.getPage(n);
        const scale = targetWidth / page.getViewport({ scale: 1 }).width;
        const viewport = page.getViewport({ scale: scale * dpr });

        const canvas = document.createElement("canvas");
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.className = "mb-3 block w-full rounded-lg border border-line";
        container.appendChild(canvas);

        const ctx = canvas.getContext("2d");
        if (!ctx) continue;
        await page.render({ canvasContext: ctx, canvas, viewport }).promise;
      }
    }

    render().catch((err) => {
      if (!cancelled) {
        console.error(err);
        setError("This document could not be rendered.");
      }
    });

    return () => {
      cancelled = true;
      loadingTask?.destroy();
    };
  }, [url]);

  if (error) return <p className="text-sm text-red-600">{error}</p>;
  return <div ref={containerRef} />;
}

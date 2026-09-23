/**
 * Hand the split viewer a short-lived signed URL for one of a paper's own
 * documents (question paper, mark scheme, examiner report, ...).
 *
 * Not a route that returns bytes: unlike a question crop, a full paper PDF
 * is tens of pages, and PDF.js already knows how to fetch a document's pages
 * lazily over HTTP range requests. Proxying every one of those requests
 * through this server would buy nothing — Supabase's own storage CDN can
 * answer them directly once the client has a URL — so this route resolves
 * the URL and steps out of the way. The URL itself is never cached or baked
 * into a page (see `PaperPage`'s own note on why): it is fetched fresh, every
 * time the viewer opens.
 */

import { NextResponse } from "next/server";

import { getPaperDocumentStorageKey } from "@/lib/data/catalog";
import type { PaperDocType } from "@/lib/data/types";
import { signedUrls } from "@/lib/storage";

const DOC_TYPES = new Set<PaperDocType>(["qp", "ms", "er", "gt", "in"]);

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ paper: string; docType: string }> },
) {
  const { paper, docType } = await params;

  if (!DOC_TYPES.has(docType as PaperDocType)) {
    return NextResponse.json({ error: "unknown document type" }, { status: 400 });
  }

  const storageKey = await getPaperDocumentStorageKey(paper, docType as PaperDocType);
  if (!storageKey) {
    return NextResponse.json(
      { error: "not found" },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }

  const signed = await signedUrls([storageKey]);
  const url = signed.get(storageKey);
  if (!url) {
    return NextResponse.json(
      { error: "not found" },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }

  return NextResponse.json({ url }, { headers: { "Cache-Control": "no-store" } });
}

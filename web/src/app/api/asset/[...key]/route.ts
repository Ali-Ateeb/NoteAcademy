/**
 * Serve a question crop from the private bucket.
 *
 * Why a route rather than putting signed URLs in the pages: the pages are
 * statically generated, and a signed URL expires. Baking one into a prerendered
 * page ships a link that stops working an hour after the build. The storage key
 * is stable, so the page carries the key and this resolves it at the moment
 * someone actually looks.
 *
 * The gate is the important part. Storage keys are guessable — anyone can work
 * out `papers/5054/2019-mj/p11/crops/7.png` — so signing whatever is asked for
 * would publish the unapproved half of the bank to anyone who tried. Instead
 * the key is looked up through the *anonymous* client, whose view of
 * `questions` is exactly what row level security allows: approved only. If the
 * anon role cannot see a question with this crop, neither can the requester.
 */

import { NextResponse } from "next/server";

import { assetIsPublic } from "@/lib/data/catalog";
import { SIGNED_URL_TTL_SECONDS, signedUrl } from "@/lib/storage";

/** A crop is immutable for as long as its signed URL lives, and the redirect
 *  itself is cheap. Cached privately so a shared cache never holds a URL minted
 *  for someone else. */
const CACHE_CONTROL = `private, max-age=${Math.floor(SIGNED_URL_TTL_SECONDS / 2)}`;

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ key: string[] }> },
) {
  const { key } = await params;
  const storageKey = key.map(decodeURIComponent).join("/");

  // Path traversal would only ever reach another object in the same bucket, but
  // the bucket is the thing being protected, so refuse rather than reason about
  // what it could reach.
  if (!storageKey || storageKey.includes("..")) {
    return new NextResponse("Not found", { status: 404 });
  }

  if (!(await assetIsPublic(storageKey))) {
    // Deliberately indistinguishable from a key that does not exist: whether a
    // question is merely awaiting review is not something to leak either.
    return new NextResponse("Not found", { status: 404 });
  }

  const url = await signedUrl(storageKey);
  if (!url) return new NextResponse("Not found", { status: 404 });

  return NextResponse.redirect(url, {
    status: 302,
    headers: { "Cache-Control": CACHE_CONTROL },
  });
}

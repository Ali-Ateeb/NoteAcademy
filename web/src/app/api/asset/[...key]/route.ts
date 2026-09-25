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
 *
 * The response is the crop's own bytes, not a redirect to a signed URL. A
 * signed URL is single-use in every sense that matters here — no shared
 * cache can serve it to a second viewer, because caching it would leak that
 * viewer's URL to whoever reads the cache, and its own short TTL means it
 * cannot be reused later anyway — so the old 302 cost *every* view of *every*
 * crop, by every student, two full round trips and one invocation of this
 * function apiece. Downloading the object here and returning it directly
 * costs one round trip, and — because approved content is exactly what RLS
 * already intends to be public, the same reasoning that already let
 * `questions_public`, `question_assets_public` etc. answer anyone without
 * checking who is asking — the response can carry a `public` Cache-Control a
 * CDN is allowed to share across every viewer who asks for the same crop, so
 * only the first of them ever reaches this function at all.
 */

import { NextResponse } from "next/server";

import { assetIsPublic } from "@/lib/data/catalog";
import { downloadAsset, SIGNED_URL_TTL_SECONDS } from "@/lib/storage";

/** Public, not private: unlike a signed URL, the bytes returned here carry no
 *  per-request secret, so a shared cache holding them serves every viewer the
 *  same approved content RLS already lets any of them read directly. The TTL
 *  is the same window a review-queue signed URL gets — long enough that a
 *  popular crop is fetched once per cache rather than once per view, short
 *  enough that re-uploading a corrected crop (a wrong bbox, a bad render)
 *  reaches every viewer within the hour instead of sitting cached forever. */
// s-maxage is the shared-cache (CDN) counterpart of max-age: without it a CDN in
// front of this route is free to ignore the response and call the function for
// every view. stale-while-revalidate lets it keep serving a crop while it
// refetches one that has just expired, so no viewer waits on the storage read.
const CACHE_CONTROL =
  `public, max-age=${SIGNED_URL_TTL_SECONDS}, s-maxage=${SIGNED_URL_TTL_SECONDS}, stale-while-revalidate=86400`;

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
    return new NextResponse("Not found", { status: 404, headers: { "Cache-Control": "no-store" } });
  }

  // Started together, not one after the other: they are independent round
  // trips to Supabase, and waiting for the gate before starting the download
  // put both latencies in every first view of a crop. The bytes are held back
  // until the gate has answered, so an unapproved crop is fetched and then
  // discarded, never returned.
  const [isPublic, asset] = await Promise.all([
    assetIsPublic(storageKey),
    downloadAsset(storageKey),
  ]);

  if (!isPublic) {
    // Deliberately indistinguishable from a key that does not exist: whether a
    // question is merely awaiting review is not something to leak either. Not
    // cached either way: an unapproved question can be approved at any
    // moment, and a cached 404 would hide it past that.
    return new NextResponse("Not found", { status: 404, headers: { "Cache-Control": "no-store" } });
  }

  if (!asset) {
    return new NextResponse("Not found", { status: 404, headers: { "Cache-Control": "no-store" } });
  }

  return new NextResponse(asset, {
    status: 200,
    headers: {
      "Content-Type": asset.type || "image/png",
      "Cache-Control": CACHE_CONTROL,
    },
  });
}

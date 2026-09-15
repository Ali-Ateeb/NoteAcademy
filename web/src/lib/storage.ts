/**
 * Access to the private asset bucket, service-role only.
 *
 * The bucket is private and stays that way. A public bucket would put every
 * question crop on a permanent, guessable URL outside row level security
 * entirely — which is the same as publishing the unapproved half of the bank.
 * Nothing here hands out a bucket URL of any kind: `/api/asset` checks
 * whether the caller may see the object (through RLS, via `assetIsPublic`)
 * and, only then, downloads it and returns the bytes itself — see
 * `downloadAsset`'s own docstring for why that replaced signing a one-off
 * URL and redirecting to it. `signedUrls` remains for the one surface that
 * genuinely needs a URL rather than bytes: the review queue embeds several
 * dozen crops directly into one server-rendered page.
 *
 * Server-side only. Every function here needs the service role key, and that
 * key must never reach a browser bundle.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const BUCKET = process.env.STORAGE_BUCKET || "noteacademy-papers";

/** How long a review-queue signed URL lasts, and the ceiling this project
 *  uses for how long a downloaded crop may sit in a cache (see `/api/asset`) —
 *  long enough that a popular question's crop is barely ever fetched twice,
 *  short enough that a corrected re-upload (a wrong bbox, a bad crop) reaches
 *  every viewer within the hour rather than being invisible indefinitely. */
export const SIGNED_URL_TTL_SECONDS = 60 * 60;

function serviceKey(): string {
  return typeof window === "undefined" ? process.env.SUPABASE_SERVICE_ROLE_KEY ?? "" : "";
}

let cached: SupabaseClient | null | undefined;

function storageClient(): SupabaseClient | null {
  if (cached === undefined) {
    const key = serviceKey();
    cached =
      SUPABASE_URL && key
        ? createClient(SUPABASE_URL, key, {
            auth: { persistSession: false, autoRefreshToken: false },
          })
        : null;
  }
  return cached;
}

export function isStorageConfigured(): boolean {
  return storageClient() !== null;
}

/** The object's own bytes, straight from the bucket. Null when storage is not
 *  configured or the object is absent — a missing crop is a page without an
 *  image, never a page that fails.
 *
 *  Used by `/api/asset` instead of a signed URL + redirect: that pattern cost
 *  a browser two round trips per crop (fetch the route, follow the redirect,
 *  fetch the object from Supabase's own host) and, because the redirect
 *  target was single-use and short-lived, could never be cached by anything
 *  shared between viewers — so a crop paid this cost on *every* view, by
 *  every student, forever. Downloading the bytes here and returning them
 *  directly collapses that to one round trip, and — because approved
 *  content is exactly what RLS already intends to be public — the response
 *  can carry a `public` Cache-Control a CDN is allowed to share across
 *  viewers, so a popular crop's second view onward costs nothing at all. */
export async function downloadAsset(storageKey: string): Promise<Blob | null> {
  const client = storageClient();
  if (!client) return null;

  const { data, error } = await client.storage.from(BUCKET).download(storageKey);
  if (error || !data) {
    if (error) console.warn(`downloading ${storageKey} failed: ${error.message}`);
    return null;
  }
  return data;
}

/** Supabase refuses a batch of more than 1000 paths, and refuses the *whole*
 *  request — so an over-long batch does not sign 1000 of them, it signs none.
 *  Chunked well under the cap: the failure it prevents is total, and the cost
 *  of an extra round trip per 500 crops is nothing. */
const SIGN_BATCH = 500;

/** Sign many objects.
 *
 *  The review queue shows a whole paper of crops at a time; signing them one at
 *  a time is forty round trips before the page can render. */
export async function signedUrls(
  storageKeys: string[],
  expiresIn: number = SIGNED_URL_TTL_SECONDS,
): Promise<Map<string, string>> {
  const signed = new Map<string, string>();
  const client = storageClient();
  const keys = [...new Set(storageKeys)];
  if (!client || keys.length === 0) return signed;

  for (let from = 0; from < keys.length; from += SIGN_BATCH) {
    const batch = keys.slice(from, from + SIGN_BATCH);
    const { data, error } = await client.storage
      .from(BUCKET)
      .createSignedUrls(batch, expiresIn);

    if (error) {
      // One bad batch costs its own crops, not every crop on the page.
      console.warn(`signing ${batch.length} objects failed: ${error.message}`);
      continue;
    }

    for (const entry of data ?? []) {
      // The API returns a row per key, carrying its own error for the ones that
      // are missing. A crop that was never uploaded is expected, not exceptional.
      if (entry.signedUrl && entry.path) signed.set(entry.path, entry.signedUrl);
    }
  }
  return signed;
}

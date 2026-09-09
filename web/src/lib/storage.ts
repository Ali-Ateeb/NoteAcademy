/**
 * Signed URLs for the private asset bucket.
 *
 * The bucket is private and stays that way. A public bucket would put every
 * question crop on a permanent, guessable URL outside row level security
 * entirely — which is the same as publishing the unapproved half of the bank.
 * So nothing is served directly: the app mints a short-lived signed URL for the
 * specific object it is about to show.
 *
 * Server-side only. Every function here needs the service role key, and that
 * key must never reach a browser bundle.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const BUCKET = process.env.STORAGE_BUCKET || "noteacademy-papers";

/** Long enough to look at a page of questions, short enough that a leaked URL
 *  stops working before it can be shared usefully. */
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

/** Sign one object. Null when storage is not configured or the key is absent —
 *  a missing crop is a page without an image, never a page that fails. */
export async function signedUrl(
  storageKey: string,
  expiresIn: number = SIGNED_URL_TTL_SECONDS,
): Promise<string | null> {
  const client = storageClient();
  if (!client) return null;

  const { data, error } = await client.storage
    .from(BUCKET)
    .createSignedUrl(storageKey, expiresIn);

  if (error) {
    console.warn(`signing ${storageKey} failed: ${error.message}`);
    return null;
  }
  return data?.signedUrl ?? null;
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

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

/** Sign many objects in one request.
 *
 *  The review queue shows a hundred crops at a time; signing them one at a time
 *  is a hundred round trips before the page can render. */
export async function signedUrls(
  storageKeys: string[],
  expiresIn: number = SIGNED_URL_TTL_SECONDS,
): Promise<Map<string, string>> {
  const signed = new Map<string, string>();
  const client = storageClient();
  const keys = [...new Set(storageKeys)];
  if (!client || keys.length === 0) return signed;

  const { data, error } = await client.storage
    .from(BUCKET)
    .createSignedUrls(keys, expiresIn);

  if (error) {
    console.warn(`signing ${keys.length} objects failed: ${error.message}`);
    return signed;
  }

  for (const entry of data ?? []) {
    // The API returns a row per key, carrying its own error for the ones that
    // are missing. A crop that was never uploaded is expected, not exceptional.
    if (entry.signedUrl && entry.path) signed.set(entry.path, entry.signedUrl);
  }
  return signed;
}

/**
 * Where a question crop is fetched from.
 *
 * Two ways to reach the same file:
 *
 *  - `/api/asset/<key>`: a function that checks the crop belongs to an approved
 *    question, downloads it from the private bucket and returns the bytes.
 *    Always works; costs a function call, a database check and a storage read
 *    per view (1-2.8 s cold, measured).
 *  - the public bucket's own URL: a plain file on Supabase's CDN, no function
 *    in between. Only holds crops of *approved* questions (`noteacademy
 *    sync-public-crops`, and the review route as decisions are made), which
 *    is exactly what a page is allowed to show.
 *
 * The public URL is used only when `NEXT_PUBLIC_CROP_BUCKET` is set, and any
 * image that fails to load from it falls back to the gated route, so a crop
 * that has not been copied over yet is slow, never missing.
 *
 * Safe to import from client components: it reads only public env vars.
 */

const SUPABASE_URL = (process.env.NEXT_PUBLIC_SUPABASE_URL ?? "").replace(/\/+$/, "");
const CROP_BUCKET = process.env.NEXT_PUBLIC_CROP_BUCKET ?? "";

const PUBLIC_MARKER = "/storage/v1/object/public/";

function encodeKey(storageKey: string): string {
  return storageKey.split("/").map(encodeURIComponent).join("/");
}

export function cropUrl(storageKey: string | null): string | null {
  if (!storageKey) return null;
  if (SUPABASE_URL && CROP_BUCKET) {
    return `${SUPABASE_URL}${PUBLIC_MARKER}${encodeURIComponent(CROP_BUCKET)}/${encodeKey(storageKey)}`;
  }
  return `/api/asset/${encodeKey(storageKey)}`;
}

/** The gated route's URL for a public-bucket URL, or null for a URL that is not
 *  one (already the gated route, or something else entirely). */
export function gatedUrlFor(url: string): string | null {
  const at = url.indexOf(PUBLIC_MARKER);
  if (at === -1) return null;
  const rest = url.slice(at + PUBLIC_MARKER.length); // "<bucket>/<encoded key>"
  const slash = rest.indexOf("/");
  return slash === -1 ? null : `/api/asset/${rest.slice(slash + 1)}`;
}

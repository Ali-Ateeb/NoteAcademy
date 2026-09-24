/**
 * The site's own absolute URL, for the things that must name it in full:
 * `robots.txt`, the sitemap, and `metadataBase` (which turns the relative
 * Open Graph image path into an absolute one).
 *
 * `NEXT_PUBLIC_SITE_URL` is the real answer -- set it to the public origin
 * (`https://noteacademy.example`) wherever this is deployed. Without it the
 * build falls back to Vercel's own production hostname, then to localhost, so
 * a build never fails for want of it -- but a sitemap that says `localhost` is
 * worse than none, which is why the sitemap route warns when it is used.
 */

function fromEnvironment(): { url: string; configured: boolean } {
  const explicit = process.env.NEXT_PUBLIC_SITE_URL?.trim();
  if (explicit) return { url: explicit, configured: true };

  const vercel = process.env.VERCEL_PROJECT_PRODUCTION_URL?.trim();
  if (vercel) return { url: `https://${vercel}`, configured: true };

  return { url: "http://localhost:3000", configured: false };
}

const resolved = fromEnvironment();

/** No trailing slash, so `${SITE_URL}/path` is always well formed. */
export const SITE_URL = resolved.url.replace(/\/+$/, "");

/** False when this is the localhost fallback rather than a real origin. */
export const SITE_URL_CONFIGURED = resolved.configured;

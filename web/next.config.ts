import type { NextConfig } from "next";

// Sent on every response. Nothing here restricts what the page itself may load
// -- a script/style/connect policy would have to allow Next's inline hydration
// data and Supabase, and one that allows 'unsafe-inline' scripts buys little --
// so this is the set that costs nothing and closes real holes:
//
//   * frame-ancestors / X-Frame-Options: nobody may embed this site in a frame.
//     Without it the reviewer queue's approve/reject buttons can be put under a
//     transparent page on another origin and clicked by someone who thinks
//     they are clicking something else. Nothing here frames anything itself.
//   * base-uri / form-action / object-src: no injected <base> re-pointing
//     relative URLs, no form posting off-site, no plugin embeds.
//   * nosniff: a response is only ever treated as the type it declares.
//   * Referrer-Policy: other sites see this one's origin, not the full path of
//     a page (which can carry a paper or topic a student is working on).
//   * Permissions-Policy: nothing here uses the camera, microphone or location,
//     so nothing embedded gets to ask. (Payment is left alone: it is roadmap.)
//   * HSTS: once a browser has seen this over https it will not go back to http.
//     No includeSubDomains/preload -- those are commitments about domains this
//     file cannot see.
const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
  },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Strict-Transport-Security", value: "max-age=31536000" },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Do not advertise the framework and version on every response.
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  // A production build writes over whatever is in the build directory, and
  // `next dev` serves out of that same directory while it runs — so verifying a
  // change with `npm run build` while someone has `npm run dev` open kills their
  // server with "Cannot find module './611.js'", which reads like a broken app
  // rather than two commands sharing a folder.
  //
  // NEXT_DIST_DIR gives the checks somewhere else to write:
  //   NEXT_DIST_DIR=.next-verify npm run build
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Paper pages are the entire acquisition channel: "5054 may june 2019 paper 12"
  // is a real search query, and tens of thousands of statically generated,
  // indexable pages is the marketing budget. Keep them static.
  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;

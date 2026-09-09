import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
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

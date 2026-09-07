import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Paper pages are the entire acquisition channel: "5054 may june 2019 paper 12"
  // is a real search query, and tens of thousands of statically generated,
  // indexable pages is the marketing budget. Keep them static.
  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;

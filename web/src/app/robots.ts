import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/siteUrl";

/**
 * Everything a search engine should read is a paper, topic or subject page;
 * everything else is either private (accounts, the reviewer queue), an API, or
 * an interactive tool with nothing to index -- a timed arena has no content
 * until someone sits it, and crawling one just starts a clock.
 *
 * `Disallow` is a request to well-behaved crawlers, not access control: the
 * reviewer queue and APIs are protected by their own auth, not by this file.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: [
          "/api/",
          "/admin/",
          "/auth/",
          "/dashboard",
          "/login",
          "/signup",
          "/forgot-password",
          "/reset-password",
          "/practice/",
          "/topics/*/*/practice",
        ],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}

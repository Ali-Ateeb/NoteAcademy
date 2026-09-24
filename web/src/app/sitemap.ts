import type { MetadataRoute } from "next";

import { getPapers, getSubjects, revisableTopics } from "@/lib/data/catalog";
import { SITE_URL, SITE_URL_CONFIGURED } from "@/lib/siteUrl";

/** Regenerated at most daily; approving questions changes what a page
 *  contains, not which pages exist, so this does not need to track it. */
export const revalidate = 86400;

/**
 * Every page worth indexing, and only those: the landing page, the subject
 * directory, and -- for each *published* subject, the same set the pages
 * themselves are statically generated for -- the subject hub, every paper, and
 * every topic that has something to practise. The arenas and account pages are
 * deliberately absent (see `robots.ts`).
 *
 * "5054 may june 2019 paper 12" is a real search query and each paper page is
 * built to answer it, so a crawler that cannot find the pages cannot use them.
 */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  if (!SITE_URL_CONFIGURED) {
    console.warn(
      "sitemap: NEXT_PUBLIC_SITE_URL is not set, so every URL below names " +
        `${SITE_URL}. Set it to the public origin before deploying.`,
    );
  }

  const entries: MetadataRoute.Sitemap = [
    { url: `${SITE_URL}/`, changeFrequency: "weekly", priority: 1 },
    { url: `${SITE_URL}/subjects`, changeFrequency: "weekly", priority: 0.9 },
  ];

  for (const subject of (await getSubjects()).filter((s) => s.isPublished)) {
    entries.push({
      url: `${SITE_URL}/subjects/${subject.slug}`,
      changeFrequency: "weekly",
      priority: 0.8,
    });

    for (const paper of await getPapers(subject.slug)) {
      entries.push({
        url: `${SITE_URL}/papers/${paper.slug}`,
        changeFrequency: "monthly",
        priority: 0.6,
      });
    }

    for (const topic of await revisableTopics(subject.slug)) {
      // A topic with nothing to practise is a page that says so; not one to
      // invite a crawler to (a handful of biology topics, for now).
      if (topic.questionCount === 0) continue;
      entries.push({
        url: `${SITE_URL}/topics/${subject.slug}/${topic.slug}`,
        changeFrequency: "weekly",
        priority: 0.7,
      });
    }
  }

  return entries;
}

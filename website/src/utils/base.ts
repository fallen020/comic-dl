const rawBase: string = import.meta.env.BASE_URL;

export const siteBase = rawBase.endsWith('/') ? rawBase : rawBase + '/';

export function pagePath(path: string): string {
  return siteBase + path.replace(/^\//, '');
}

// Docs routes always end in `/` (the site builds with
// `trailingSlash: 'always'`), so link builders must use this — a slash-less
// href 404s on static hosts instead of redirecting.
export function docsPath(id: string): string {
  const clean = id.replace(/^\/|\/$/g, '');
  return pagePath(clean ? `docs/${clean}/` : 'docs/');
}

// The "Edit this page" link has to name a real file under docs/. Website pages
// are a hand-maintained fork, and three slugs were renamed on the way, so the
// ones that match their docs/ filename are the exception rather than the rule.
const DOCS_SOURCE: Record<string, string> = {
  introduction: 'index.md',
  installation: 'install.md',
  'usage/basic': 'usage/download.md',
};

export function docsSourcePath(id: string): string {
  return DOCS_SOURCE[id] ?? `${id}.md`;
}
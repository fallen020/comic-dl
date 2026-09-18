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
const rawBase: string = import.meta.env.BASE_URL;

export const siteBase = rawBase.endsWith('/') ? rawBase : rawBase + '/';

export function pagePath(path: string): string {
  return siteBase + path.replace(/^\//, '');
}
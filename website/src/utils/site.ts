import { readFileSync } from 'node:fs';

interface SiteManifest {
  sites: Record<string, unknown>;
}

const root = new URL('../../../../', import.meta.url);

const pyproject = readFileSync(new URL('pyproject.toml', root), 'utf-8');
const project = pyproject.slice(pyproject.indexOf('[project]'));

/** Version of the release the site is documenting. */
export const appVersion: string = project.match(/^version\s*=\s*"([^"]+)"/m)?.[1] ?? '0.0.0';

/**
 * Built-in scraper count, read from the manifest that `update-site-manifest.py`
 * derives from the scraper registry, so the homepage cannot advertise a stale
 * number the way a hardcoded count did.
 */
export const scraperCount: number = Object.keys(
  (JSON.parse(readFileSync(new URL('site-support.json', root), 'utf-8')) as SiteManifest).sites,
).length;

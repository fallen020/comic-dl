import { getCollection } from 'astro:content';
import { docsPath, siteBase } from '../utils/base';

export const prerender = true;

// llmstxt.org indexes prose with one absolute link per page, so the index and
// the pages cannot drift the way two hand-maintained files do. Titles and
// descriptions come from the same frontmatter the page renders.
const ROOT = import.meta.env.SITE;

export async function GET(): Promise<Response> {
  const entries = (await getCollection('docs')).sort((a, b) => a.data.order - b.data.order);
  const sections: { name: string; items: string[] }[] = [];

  for (const entry of entries) {
    const name = entry.data.section || 'Documentation';
    const item = `- [${entry.data.title}](${ROOT}${docsPath(entry.id)})${
      entry.data.description ? `: ${entry.data.description}` : ''
    }`;
    const section = sections.find((s) => s.name === name);
    if (section) section.items.push(item);
    else sections.push({ name, items: [item] });
  }

  const body = [
    '# comic-dl',
    '',
    '> A command-line downloader for comics and manga. Download from supported',
    '> sites and package into CBZ, ZIP, or CBT archives.',
    '',
    `- Docs: ${ROOT}${siteBase}docs/`,
    '- Source: https://github.com/fallen020/comic-dl',
    '- Releases (binaries, no Python needed): https://github.com/fallen020/comic-dl/releases',
    '',
    ...sections.flatMap((s) => [`## ${s.name}`, '', ...s.items, '']),
  ].join('\n');

  return new Response(body, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}

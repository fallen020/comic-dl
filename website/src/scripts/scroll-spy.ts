/** Highlights the current section in every "On this page" nav on the page.
 * No-op on pages without headings. Shared by the desktop rail and the
 * mobile disclosure, which is why links are matched in both. */
export function initScrollSpy(root: ParentNode = document): void {
  const links = Array.from(
    root.querySelectorAll<HTMLAnchorElement>('.toc-link[href^="#"]'),
  );
  if (links.length === 0) return;
  const bySlug = new Map<string, HTMLAnchorElement[]>();
  for (const link of links) {
    const slug = decodeURIComponent(link.getAttribute('href')!.slice(1));
    const group = bySlug.get(slug);
    if (group) group.push(link);
    else bySlug.set(slug, [link]);
  }
  const slugs = new Set(bySlug.keys());
  let current: string | null = null;
  function activate(slug: string | null): void {
    if (slug === current) return;
    current = slug;
    for (const link of links) link.classList.remove('is-active');
    if (slug !== null) {
      for (const link of bySlug.get(slug) ?? []) link.classList.add('is-active');
    }
  }
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) activate(entry.target.id);
      }
    },
    { rootMargin: '-15% 0px -75% 0px' },
  );
  for (const heading of root.querySelectorAll<HTMLElement>(
    '.prose-docs h2[id], .prose-docs h3[id]',
  )) {
    if (slugs.has(heading.id)) observer.observe(heading);
  }
}

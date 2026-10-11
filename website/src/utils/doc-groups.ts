import type { CollectionEntry } from 'astro:content';

interface Group {
  label: string;
  order: number;
  items: CollectionEntry<'docs'>[];
}

/** Sidebar tree and the docs index both render the same section grouping, so
 * the ordering rule lives here rather than in two templates that can drift. */
export function docGroups(entries: CollectionEntry<'docs'>[]): Group[] {
  const groups = new Map<string | null, Group>();
  for (const entry of entries) {
    const label = entry.data.section ?? null;
    let group = groups.get(label);
    if (!group) {
      group = { label: label ?? '', order: entry.data.order, items: [] };
      groups.set(label, group);
    }
    group.items.push(entry);
    group.order = Math.min(group.order, entry.data.order);
  }
  return Array.from(groups.values()).sort((a, b) => a.order - b.order);
}

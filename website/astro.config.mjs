import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  site: 'https://fallen020.github.io',
  base: '/comic-dl',
  output: 'static',
  trailingSlash: 'always',
  markdown: {
    shikiConfig: {
      themes: {
        light: 'github-light',
        dark: 'github-dark',
      },
      defaultColor: false,
      wrap: false,
    },
  },
  integrations: [
    mdx(),
    sitemap({
      filter: (page) => !page.endsWith('/404/'),
    }),
  ],
  vite: {
    plugins: [tailwindcss()],
  },
});

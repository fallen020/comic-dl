/**
 * Contrast gate for the theme tokens in `src/styles/global.css`.
 *
 * Fails when a token pair drops below its WCAG minimum, or when a
 * `theme-color` meta in BaseLayout.astro drifts from the theme's canvas
 * colour (those metas are literal hex, so nothing else keeps them in sync).
 *
 * Usage: node scripts/check-contrast.mjs (run from website/)
 */

import { readFileSync } from 'node:fs';

const CSS = new URL('../src/styles/global.css', import.meta.url);
const LAYOUT = new URL('../src/layouts/BaseLayout.astro', import.meta.url);

/** [foreground, background, minimum ratio, what it is used for] */
const PAIRS = [
  ['fg', 'surface', 4.5, 'body text on canvas'],
  ['fg', 'surface-1', 4.5, 'body text on cards'],
  ['fg', 'surface-2', 4.5, 'body text on code fill'],
  ['fg-secondary', 'surface', 4.5, 'lede text'],
  ['fg-secondary', 'surface-1', 4.5, 'lede text on cards'],
  ['fg-muted', 'surface', 4.5, 'meta text'],
  ['fg-muted', 'surface-1', 4.5, 'meta text on cards'],
  ['fg-muted', 'surface-2', 4.5, 'meta text on code fill'],
  ['fg-muted', 'surface-3', 4.5, 'meta text on code header'],
  ['accent-text', 'surface', 4.5, 'prose links'],
  ['accent-text', 'surface-1', 4.5, 'links on cards'],
  ['accent-text', 'surface-2', 4.5, 'links in callouts and code'],
  ['accent-text', 'accent-subtle', 4.5, 'active nav item'],
  ['on-accent', 'accent', 4.5, 'primary button label'],
  ['accent', 'surface', 3, 'accent borders and marks'],
  ['accent', 'accent-subtle', 3, 'accent edge on the active nav item'],
  ['line-strong', 'surface', 3, 'input and control borders'],
  ['note', 'note-bg', 4.5, 'note callout'],
  ['warn', 'warn-bg', 4.5, 'warning callout'],
  ['tip', 'tip-bg', 4.5, 'tip callout'],
  ['focus', 'surface', 3, 'focus ring'],
];

/** Every `--name: light-dark(<light>, <dark>)` pair in the stylesheet. */
function tokens(css) {
  const out = {};
  for (const [, name, light, dark] of css.matchAll(
    /--([\w-]+):\s*light-dark\(\s*([^,]+),\s*([^)]+)\);/g,
  )) {
    out[name] = { light: light.trim(), dark: dark.trim() };
  }
  return out;
}

function srgbToLinear(c) {
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(hex) {
  const channels = [1, 3, 5].map((i) => srgbToLinear(parseInt(hex.slice(i, i + 2), 16) / 255));
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const palette = tokens(readFileSync(CSS, 'utf8'));
const errors = [];

for (const theme of ['light', 'dark']) {
  for (const [fg, bg, min, use] of PAIRS) {
    let missing = false;
    for (const token of [fg, bg]) {
      if (!/^#[0-9a-f]{6}$/i.test(palette[token]?.[theme] ?? '')) {
        errors.push(`${theme}: --${token} missing or not a 6-digit hex (${palette[token]?.[theme] ?? 'absent'})`);
        missing = true;
      }
    }
    if (missing) continue;
    const ratio = contrast(palette[fg][theme], palette[bg][theme]);
    if (ratio < min) {
      errors.push(`${theme}: --${fg} on --${bg} is ${ratio.toFixed(2)}:1, needs ${min}:1 (${use})`);
    }
  }
}

// theme-color metas are literal hex in the layout, so pin them to the canvas.
const layout = readFileSync(LAYOUT, 'utf8');
for (const theme of ['light', 'dark']) {
  const meta = layout.match(
    new RegExp(`<meta name="theme-color" media="\\(prefers-color-scheme: ${theme}\\)" content="([^"]+)"`),
  );
  if (!meta) errors.push(`BaseLayout: no theme-color meta for ${theme}`);
  else if (meta[1].toLowerCase() !== palette.surface?.[theme].toLowerCase()) {
    errors.push(`BaseLayout: ${theme} theme-color ${meta[1]} != --surface ${palette.surface?.[theme]}`);
  }
}

for (const error of errors) console.error(`check-contrast: ${error}`);
if (errors.length) {
  console.error(`check-contrast: ${errors.length} problem(s)`);
  process.exit(1);
}
console.log(`check-contrast: OK (${PAIRS.length} pairs x 2 themes)`);

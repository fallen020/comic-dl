async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through to legacy path */
    }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

const COPY_SVG = `<svg class="code-copy__icon code-copy__icon--copy" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>`;
const CHECK_SVG = `<svg class="code-copy__icon code-copy__icon--check" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>`;

function buttonInnerHTML(): string {
  return `<span class="code-copy__icons" aria-hidden="true">${COPY_SVG}${CHECK_SVG}</span><span class="code-copy__label">Copy</span>`;
}

function flashCopied(btn: HTMLButtonElement, ok: boolean): void {
  btn.classList.toggle('copied', ok);
  const label = btn.querySelector('.code-copy__label');
  if (label) label.textContent = ok ? 'Copied' : 'Failed';
  btn.setAttribute('aria-label', ok ? 'Copied' : 'Copy failed');
  window.setTimeout(() => {
    btn.classList.remove('copied');
    const l = btn.querySelector('.code-copy__label');
    if (l) l.textContent = 'Copy';
    btn.setAttribute('aria-label', 'Copy to clipboard');
  }, 1500);
}

function createCopyButton(getText: () => string): HTMLButtonElement {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'code-copy';
  btn.innerHTML = buttonInnerHTML();
  btn.setAttribute('aria-label', 'Copy to clipboard');
  btn.setAttribute('aria-live', 'polite');
  btn.addEventListener('click', async () => {
    const ok = await copyText(getText());
    flashCopied(btn, ok);
  });
  return btn;
}

function langOf(pre: HTMLPreElement): string {
  const code = pre.querySelector('code');
  const cls = code?.className || '';
  const m = /language-([\w-]+)/.exec(cls);
  return m ? m[1] : '';
}

/** Shell snippets show a `$` prompt that must not land in the clipboard. */
function codeText(pre: HTMLPreElement): string {
  const text = pre.textContent ?? '';
  return text
    .split('\n')
    .map((line) => line.replace(/^\$\s/, ''))
    .join('\n');
}

/**
 * Ensures every highlighted code block has a header bar with a copy button.
 * Idempotent. Skips blocks already wrapped by the <CodeBlock /> component.
 */
export function enhanceCodeBlocks(root: ParentNode = document): void {
  const pres = root.querySelectorAll<HTMLPreElement>('pre.astro-code');
  for (const pre of Array.from(pres)) {
    if (pre.closest('[data-codeblock]')) continue;
    if (pre.closest('.codeblock')) continue;
    if (pre.dataset.copyEnhanced) continue;
    pre.dataset.copyEnhanced = 'true';

    const wrap = document.createElement('div');
    wrap.className = 'codeblock';

    const header = document.createElement('div');
    header.className = 'codeblock-header';

    const label = document.createElement('span');
    label.className = 'codeblock-lang';
    label.textContent = langOf(pre) || 'terminal';
    header.appendChild(label);

    header.appendChild(createCopyButton(() => codeText(pre)));

    pre.before(wrap);
    wrap.appendChild(header);
    wrap.appendChild(pre);
  }

  // Wire up copy hooks on pre-rendered <CodeBlock /> components.
  root.querySelectorAll<HTMLElement>('[data-codeblock]').forEach((wrap) => {
    const pre = wrap.querySelector('pre');
    const hook = wrap.querySelector<HTMLButtonElement>('[data-copy-hook]');
    if (!pre || !hook || hook.dataset.wired) return;
    hook.dataset.wired = 'true';
    hook.setAttribute('aria-live', 'polite');
    hook.addEventListener('click', async () => {
      const ok = await copyText(codeText(pre));
      flashCopied(hook, ok);
    });
  });
}
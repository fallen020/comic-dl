export async function copyText(text: string): Promise<boolean> {
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

export function createCopyButton(getText: () => string): HTMLButtonElement {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'code-copy';
  btn.textContent = 'Copy';
  btn.setAttribute('aria-label', 'Copy to clipboard');
  btn.setAttribute('aria-live', 'polite');
  btn.addEventListener('click', async () => {
    const ok = await copyText(getText());
    btn.textContent = ok ? 'Copied' : 'Failed';
    btn.setAttribute('aria-label', ok ? 'Copied' : 'Copy failed');
    window.setTimeout(() => {
      btn.textContent = 'Copy';
      btn.setAttribute('aria-label', 'Copy to clipboard');
    }, 2000);
  });
  return btn;
}

function langOf(pre: HTMLPreElement): string {
  const code = pre.querySelector('code');
  const cls = code?.className || '';
  const m = /language-([\w-]+)/.exec(cls);
  return m ? m[1] : '';
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

    header.appendChild(createCopyButton(() => pre.textContent ?? ''));

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
      const ok = await copyText(pre.textContent ?? '');
      hook.textContent = ok ? 'Copied' : 'Failed';
      hook.setAttribute('aria-label', ok ? 'Copied' : 'Copy failed');
      window.setTimeout(() => {
        hook.textContent = 'Copy';
        hook.setAttribute('aria-label', 'Copy to clipboard');
      }, 2000);
    });
  });
}
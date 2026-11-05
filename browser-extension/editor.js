/* DOM ranges only. The site's editable tree, selection and IME own their normal state. */
(() => {
  const blocks = new Set(['DIV', 'P', 'LI', 'PRE', 'BLOCKQUOTE', 'SECTION', 'H1', 'H2', 'H3']);
  const textarea = (el) => el instanceof HTMLTextAreaElement;
  // Claude (claude.ai, including Claude Code on the web) only.
  const selector =
    '[data-testid="chat-input"][contenteditable], [data-testid="chat-input"] [contenteditable="true"], div.ProseMirror[contenteditable="true"], textarea[data-testid="chat-input"]';

  function composer() {
    return [...document.querySelectorAll(selector)].find(
      (el) =>
        (textarea(el) ? !el.readOnly && !el.disabled : el.isContentEditable) &&
        !el.closest('[aria-hidden="true"], [hidden]') &&
        el.getClientRects().length,
    );
  }

  function snapshot(el) {
    if (textarea(el)) return { text: el.value };
    let text = '';
    const nodes = [];
    function walk(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        nodes.push({ node, start: text.length, end: text.length + node.length });
        text += node.data;
      } else if (node.nodeType === Node.ELEMENT_NODE && node.getAttribute('contenteditable') !== 'false') {
        if (node.tagName === 'BR') {
          if (
            !node.classList.contains('ProseMirror-trailingBreak') &&
            (node.nextSibling || node.previousSibling)
          )
            text += '\n';
          return;
        }
        let previousBlock = false;
        for (const child of node.childNodes) {
          const block = blocks.has(child.nodeName);
          if (child.previousSibling && (block || previousBlock)) text += '\n';
          walk(child);
          previousBlock = block;
        }
      }
    }
    walk(el);
    return { text, nodes };
  }

  function mirror(el, parent, text) {
    const copy = document.createElement('div'),
      css = getComputedStyle(el),
      box = el.getBoundingClientRect();
    for (const key of [
      'fontFamily',
      'fontSize',
      'fontWeight',
      'fontStyle',
      'fontStretch',
      'fontVariant',
      'lineHeight',
      'letterSpacing',
      'wordSpacing',
      'textIndent',
      'textAlign',
      'textTransform',
      'direction',
      'writingMode',
      'tabSize',
      'paddingTop',
      'paddingRight',
      'paddingBottom',
      'paddingLeft',
      'borderTopWidth',
      'borderRightWidth',
      'borderBottomWidth',
      'borderLeftWidth',
    ]) {
      copy.style[key] = css[key];
    }
    Object.assign(copy.style, {
      position: 'fixed',
      left: box.left + 'px',
      top: box.top + 'px',
      height: box.height + 'px',
      boxSizing: 'border-box',
      borderStyle: 'solid',
      overflow: 'hidden',
      visibility: 'hidden',
      whiteSpace: el.wrap === 'off' ? 'pre' : 'pre-wrap',
      overflowWrap: el.wrap === 'off' ? 'normal' : 'break-word',
    });
    copy.style.width =
      el.clientWidth + parseFloat(css.borderLeftWidth) + parseFloat(css.borderRightWidth) + 'px';
    copy.textContent = text + '\u200b';
    parent.append(copy);
    copy.scrollTop = el.scrollTop;
    copy.scrollLeft = el.scrollLeft;
    return copy;
  }

  function boxes(el, snapshot, spans, parent) {
    const tmp = textarea(el) ? mirror(el, parent, snapshot.text) : null;
    const nodes = tmp ? [{ node: tmp.firstChild, start: 0, end: snapshot.text.length }] : snapshot.nodes;
    const offsets = [0];
    for (const char of snapshot.text) offsets.push(offsets[offsets.length - 1] + char.length);
    const clip = el.getBoundingClientRect(),
      results = [];
    for (const span of spans) {
      const a = offsets[span.start],
        b = offsets[span.end];
      if (!(a < b)) continue;
      const first = nodes.find((n) => n.start <= a && a < n.end);
      const last = nodes.find((n) => n.start < b && b <= n.end);
      if (!first || !last) continue;
      const range = document.createRange();
      range.setStart(first.node, a - first.start);
      range.setEnd(last.node, b - last.start);
      for (const rect of range.getClientRects()) {
        const x = Math.max(0, rect.left, clip.left),
          right = Math.min(innerWidth, rect.right, clip.right);
        const y = Math.max(0, rect.top, clip.top),
          bottom = Math.min(innerHeight, rect.bottom, clip.bottom);
        if (right > x && bottom > y) results.push({ span, x, y, w: right - x, h: bottom - y });
      }
    }
    tmp?.remove();
    return results;
  }

  function replace(el, before, after) {
    if (!el.isConnected || snapshot(el).text !== before) return false;
    el.focus();
    if (textarea(el)) {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(el, after);
      el.dispatchEvent(
        new InputEvent('input', { bubbles: true, inputType: 'insertReplacementText', data: after }),
      );
    } else {
      const range = document.createRange(),
        selection = getSelection();
      range.selectNodeContents(el);
      selection.removeAllRanges();
      selection.addRange(range);
      // A real editing transaction updates ProseMirror / the site's model and undo stack.
      if (!document.execCommand('insertText', false, after)) return false;
    }
    return snapshot(el).text === after;
  }
  function stableRanges(spans, before, after) {
    if (before === after) return spans;
    const a = Array.from(before),
      b = Array.from(after),
      n = Math.min(a.length, b.length);
    let prefix = 0,
      suffix = 0;
    while (prefix < n && a[prefix] === b[prefix]) prefix++;
    while (suffix < n - prefix && a[a.length - 1 - suffix] === b[b.length - 1 - suffix]) suffix++;
    const delta = b.length - a.length,
      stable = [];
    for (const s of spans) {
      if (s.end < prefix) stable.push(s);
      else if (s.start > a.length - suffix) stable.push({ ...s, start: s.start + delta, end: s.end + delta });
    }
    return stable;
  }
  globalThis.KPGuardEditor = { composer, snapshot, boxes, replace, stableRanges };
})();

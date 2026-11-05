(() => {
  'use strict';
  const editor = globalThis.KPGuardEditor,
    api = globalThis.browser || chrome;
  const documentId = crypto.randomUUID();
  const host = document.createElement('div');
  host.dataset.kpguardStatus = '';
  host.style.cssText =
    'all:initial!important;position:fixed!important;inset:0!important;pointer-events:none!important;z-index:2147483647!important';
  const shadow = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = [
    ':host{color-scheme:light dark}*{box-sizing:border-box}.marks{pointer-events:none}',
    '.mark{appearance:none;position:fixed;pointer-events:auto;cursor:pointer;border:0;border-radius:3px;border-bottom:2px solid #d29422;background:#ffc24340;padding:0;margin:0}.mark:focus-visible{outline:2px solid #347de8}',
    '.chip{position:fixed;pointer-events:auto;background:#202125;color:#f4f5f8;border:1px solid #585a61;box-shadow:0 4px 18px #0004;border-radius:12px;padding:10px;max-width:min(370px,96vw);max-height:65vh;overflow:auto;font:13px/1.45 system-ui}.chip[hidden]{display:none}',
    '.chip button,.chip select{font:inherit;border-radius:7px;border:1px solid #666971;color:#fafafa;background:#35373c;padding:5px 8px;cursor:pointer}.chip button:focus-visible{outline:2px solid #f6a623}',
    '.chip button.primary{background:#f6b246;color:#201607;border-color:transparent}.row{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.chip label{display:inline-flex;align-items:center;gap:4px}.chip textarea{width:100%;resize:vertical;background:#15161a;color:#fff}.chip pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:110px;overflow:auto}.note{position:fixed;pointer-events:auto;color:white;background:#352d21;border:1px solid #826731;padding:5px 9px;border-radius:7px;font:12px system-ui;max-width:80vw}',
  ].join('');
  const marks = document.createElement('div');
  marks.className = 'marks';
  const chip = document.createElement('section');
  chip.className = 'chip';
  chip.hidden = true;
  chip.setAttribute('role', 'dialog');
  chip.setAttribute('aria-label', 'KP Guard 작업');
  const note = document.createElement('div');
  note.className = 'note';
  note.setAttribute('role', 'status');
  note.hidden = true;
  shadow.append(style, marks, chip, note);
  document.documentElement.append(host);
  let el,
    value = '',
    version = 0,
    checked = -1,
    busy = false,
    spans = [],
    coverage = '',
    paintJob = 0,
    selected = null;
  let restoreTicket = '',
    generation = 0,
    lastURL = location.href;
  const msg = async (action, body = {}) => {
    const r = await api.runtime.sendMessage({ action, document: documentId, ...body });
    if (!r || r.error) throw new Error(r?.error || 'KP Guard 연결이 끊겼습니다.');
    return r.value;
  };
  const read = () => (el ? editor.snapshot(el).text : '');
  function close() {
    generation++;
    selected = null;
    chip.hidden = true;
    chip.replaceChildren();
  }
  function report(info) {
    note.textContent = info;
    note.hidden = !info || !el;
    if (!note.hidden) {
      const box = el.getBoundingClientRect();
      Object.assign(note.style, {
        left: Math.max(4, box.left) + 'px',
        top: Math.max(4, box.top - 28) + 'px',
      });
    }
  }
  function position() {
    if (!selected) return;
    const button = [...marks.children].find((b) => b.dataset.start === String(selected.start));
    if (!button) {
      close();
      return;
    }
    const hit = button.getBoundingClientRect(),
      panel = chip.getBoundingClientRect();
    chip.style.left = Math.max(4, Math.min(hit.left, innerWidth - panel.width - 4)) + 'px';
    chip.style.top =
      (hit.bottom + panel.height + 6 <= innerHeight
        ? hit.bottom + 5
        : Math.max(4, hit.top - panel.height - 5)) + 'px';
  }
  function paint() {
    paintJob = 0;
    marks.replaceChildren();
    if (!el?.isConnected || !spans.length) return;
    const snap = editor.snapshot(el);
    if (snap.text !== value) {
      changed();
      return;
    }
    const used = new Set();
    for (const box of editor.boxes(el, snap, spans, shadow)) {
      const b = document.createElement('button');
      b.className = 'mark';
      b.type = 'button';
      b.setAttribute('aria-label', box.span.label + ' · 작업 열기');
      b.dataset.start = String(box.span.start);
      if (used.has(box.span.start)) b.tabIndex = -1;
      used.add(box.span.start);
      Object.assign(b.style, {
        left: box.x + 'px',
        top: box.y + 'px',
        width: box.w + 'px',
        height: box.h + 'px',
      });
      b.onpointerdown = (e) => e.preventDefault();
      b.onclick = (e) => {
        if (e.isTrusted) show(box.span);
      };
      marks.append(b);
    }
    position();
  }
  function repaint() {
    if (!paintJob) paintJob = requestAnimationFrame(paint);
  }
  async function pump() {
    if (busy || !el || !value.trim() || checked === version || (!document.hasFocus() && document.hidden))
      return;
    busy = true;
    const at = version,
      text = value,
      field = el;
    try {
      const result = await msg('preview', { text });
      if (lastActive && field === el && read() === value) {
        spans = editor.stableRanges(result.spans, text, value);
        coverage = result.coverage;
        report(
          result.unlocated ? '위치를 확정할 수 없는 항목 ' + result.unlocated + '건 · 상세 점검 필요' : '',
        );
        repaint();
      }
    } catch (e) {
      if (at === version) {
        spans = [];
        report(e.message);
        repaint();
      }
    } finally {
      checked = at;
      busy = false;
      if (version !== at) void pump();
    }
  }
  function changed() {
    const next = editor.composer(),
      text = next ? editor.snapshot(next).text : '';
    if (el === next && text === value) return;
    spans = el === next ? editor.stableRanges(spans, value, text) : [];
    if (el !== next) {
      if (el) resize.unobserve(el);
      if (next) resize.observe(next);
    }
    el = next;
    value = text;
    version++;
    close();
    report('');
    repaint();
    void pump(); // Start on the input event; while busy, only the newest revision is queued.
  }
  function node(tag, text, parent = chip) {
    const n = document.createElement(tag);
    if (text) n.textContent = text;
    parent.append(n);
    return n;
  }
  function row() {
    const n = node('div');
    n.className = 'row';
    return n;
  }
  function button(parent, label, run, primary = false) {
    const b = node('button', label, parent);
    b.type = 'button';
    if (primary) b.className = 'primary';
    b.onclick = async (e) => {
      if (!e.isTrusted || b.disabled) return;
      b.disabled = true;
      try {
        await run();
      } catch (err) {
        if (!chip.hidden) node('p', err.message);
      } finally {
        b.disabled = false;
        position();
      }
    };
    return b;
  }
  function show(span) {
    close();
    selected = span;
    chip.hidden = false;
    node('strong', span.label + ' · ' + coverage);
    const main = row();
    button(main, '가릴 항목 선택', inspect, true);
    button(main, '상세 점검', () => msg('detail', { text: value }));
    const extras = row();
    button(extras, '답변 복원', restorePanel);
    button(extras, '엑셀 보호', () => msg('files'));
    button(extras, '서버 기능 전체', () => msg('server_tools'));
    button(extras, '설정', () => msg('settings'));
    button(extras, '닫기', close);
    position();
  }
  async function inspect() {
    const id = generation,
      text = value,
      at = version,
      result = await msg('inspect', { text });
    if (id !== generation || at !== version || read() !== text) return;
    chip.replaceChildren();
    node('strong', selected.label + ' · ' + result.coverage);
    const preview = node('pre', result.text);
    let previewVersion = 0,
      shownVersion = 0;
    if (result.remaining) node('p', '추가 확인이 필요한 항목 ' + result.remaining + '건');
    const keep = new Set(),
      choices = row();
    for (const token of result.choices) {
      const label = node('label', '', choices),
        input = node('input', '', label);
      input.type = 'checkbox';
      input.checked = true;
      label.append(document.createTextNode(token + ' 가리기'));
      input.onchange = async () => {
        if (input.checked) keep.delete(token);
        else keep.add(token);
        const requested = ++previewVersion;
        try {
          const next = await msg('selection', { ticket: result.ticket, keep: [...keep] });
          if (requested === previewVersion && id === generation) {
            preview.textContent = next.text;
            shownVersion = requested;
          }
        } catch (error) {
          if (id === generation) preview.textContent = error.message;
        }
      };
    }
    const reasons = node('select');
    reasons.setAttribute('aria-label', '원문 유지 사유');
    for (const [v, name] of [
      ['', '원문 유지 사유 선택'],
      ['false_positive', '오탐'],
      ['business_need', '업무상 필요'],
      ['approved', '승인받음'],
    ]) {
      node('option', name, reasons).value = v;
    }
    const actions = row();
    let deciding = false;
    async function decide(choice) {
      if (deciding) return;
      if (choice === 'apply' && shownVersion !== previewVersion)
        throw new Error('선택한 항목의 미리보기를 다시 확인해 주세요.');
      if (read() !== text || at !== version) {
        changed();
        return;
      }
      if ((choice === 'original' || (choice === 'apply' && keep.size)) && !reasons.value)
        throw new Error('원문 유지 사유를 선택해 주세요.');
      deciding = true;
      try {
        const out = await msg('decide', {
          ticket: result.ticket,
          choice,
          keep: choice === 'apply' ? [...keep] : [],
          reason: reasons.value || null,
        });
        if (id !== generation || at !== version || read() !== text) return;
        if (choice === 'apply') {
          if (!editor.replace(el, text, out.text)) {
            report('입력창이 변경되었습니다. 상세 점검에서 변환문을 확인해 주세요.');
          }
          if (out.canRestore) restoreTicket = result.ticket;
        }
        close();
        changed();
      } finally {
        deciding = false;
      }
    }
    button(actions, '입력창에 적용', () => decide('apply'), true);
    button(actions, '원문 유지', () => decide('original'));
    button(actions, '취소', () => decide('cancel'));
    position();
  }
  function restorePanel() {
    if (!restoreTicket) return msg('desktop_restore', {});
    const id = generation;
    chip.replaceChildren();
    node('strong', 'AI 답변 복원');
    const input = node('textarea');
    input.rows = 3;
    input.setAttribute('aria-label', '복원할 AI 답변');
    const actions = row();
    button(
      actions,
      '복원',
      async () => {
        const out = await msg('restore', { ticket: restoreTicket, text: input.value });
        restoreTicket = '';
        if (id === generation) {
          input.value = out.text;
          input.readOnly = true;
          input.select();
        }
      },
      true,
    );
    button(actions, '닫기', close);
    position();
  }
  // Restore remains available even after applying the final highlight.
  api.runtime.onMessage.addListener((message) => {
    if (message.action === 'openRestore') {
      if (restoreTicket && el) {
        close();
        chip.hidden = false;
        chip.style.left = '12px';
        chip.style.top = '12px';
        restorePanel();
      } else void msg('desktop_restore').catch((e) => report(e.message));
    }
  });
  document.addEventListener(
    'pointerdown',
    (e) => {
      if (!e.composedPath().includes(host)) close();
    },
    true,
  );
  document.addEventListener(
    'keydown',
    (e) => {
      if (e.key === 'Escape' && !chip.hidden) close();
    },
    true,
  );
  const resize = new ResizeObserver(repaint);
  document.addEventListener('input', changed, true);
  document.addEventListener('compositionend', changed, true);
  document.addEventListener('focusin', changed, true);
  window.addEventListener('resize', repaint);
  document.addEventListener('scroll', repaint, true);
  // SPA remounts may replace the editor without an input event.
  let domJob = 0;
  new MutationObserver(() => {
    if (domJob) return;
    domJob = requestAnimationFrame(() => {
      domJob = 0;
      if (location.href !== lastURL) {
        lastURL = location.href;
        restoreTicket = '';
        el = null;
        version++;
        close();
        spans = [];
      }
      changed();
      if (spans.length) repaint();
    });
  }).observe(document.documentElement, { subtree: true, childList: true, characterData: true });
  let lastActive = true;
  setInterval(async () => {
    if (document.hidden || !el || !value.trim()) return;
    try {
      const state = await msg('status');
      if (state.active !== lastActive) {
        lastActive = state.active;
        version++;
        close();
        spans = [];
        repaint();
        if (state.active) {
          checked = -1;
          void pump();
        } else {
          checked = version;
          report('KP Guard 감시 일시정지');
        }
      }
    } catch {
      /* The next input reports connection errors inline. */
    }
  }, 3000);
  api.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !changes.key) return;
    version++;
    checked = -1;
    close();
    spans = [];
    restoreTicket = '';
    repaint();
    if (changes.key.newValue) void pump();
    else report('KP Guard 연결을 해제했습니다.');
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) void pump();
  });
  window.addEventListener('pageshow', changed);
  window.addEventListener('pagehide', () => {
    el = null;
    value = '';
    version++;
    restoreTicket = '';
    close();
    spans = [];
    marks.replaceChildren();
  });
  changed();
})();

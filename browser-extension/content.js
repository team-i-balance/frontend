/* AI 누리 보안 · 입력 중 하이라이트와 칩 (ChatGPT·Claude 입력창).

   칩 흐름 — 새 창 없이 칩을 펼쳐 마무리한다:
     하이라이트 클릭 → 마스킹 / 원문유지
       마스킹 → 바뀔 값만 보여 주고 적용 / 모두 적용 → 입력창의 그 자리만 바꾼다
       원문유지 → 업무상 필요 / 오탐 / 승인된 개인정보
     끝나면 결과 한 줄 + 다음 항목 / 되돌리기
   하이라이트가 남아 있는 동안 Enter(전송)와 전송 버튼을 막고 첫 항목의 칩을 연다. Shift+Enter 줄바꿈은 그대로. */
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
  // 디자인 설정(docs/DESIGN-SETTINGS.md): 패널 #25292D·선 #3A4148·모서리 12, 칩은 알약 32px, 시그널 블루는 하이라이트·포커스만.
  style.textContent = [
    ':host{color-scheme:dark}*{box-sizing:border-box}.marks{pointer-events:none}',
    '.mark{appearance:none;position:fixed;pointer-events:auto;cursor:pointer;border:0;border-radius:2px;border-bottom:2px solid #3d7ef7;background:rgba(61,126,247,.22);padding:0;margin:0}',
    '.mark:hover{background:rgba(61,126,247,.32)}.mark:focus-visible{outline:2px solid #8fb5ff;outline-offset:1px}',
    ".chip,.note{font-family:'Pretendard Variable',Pretendard,-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;font-variant-numeric:tabular-nums;word-break:keep-all;overflow-wrap:anywhere;-webkit-font-smoothing:antialiased}",
    '.chip{position:fixed;pointer-events:auto;background:#25292d;color:#f1f3f5;border:1px solid #3a4148;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,.4);padding:12px;width:max-content;min-width:180px;max-width:min(360px,calc(100vw - 16px));max-height:65vh;overflow:auto;font-size:14px;font-weight:400;line-height:1.5;animation:pop 160ms cubic-bezier(.2,.8,.2,1)}',
    '.chip[hidden]{display:none}',
    '.head{display:flex;align-items:center;gap:4px;min-height:20px;margin-bottom:8px;font-size:12px;font-weight:500;color:#a3abb4}',
    '.back{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;margin:-4px 0 -4px -6px;border:0;border-radius:6px;background:transparent;color:#c1c7ce;cursor:pointer}',
    '.back:hover{background:#2e3439;color:#f1f3f5}',
    '.line{display:flex;align-items:flex-start;gap:6px;margin-bottom:8px;font-size:13px;font-weight:500;color:#f1f3f5}.line svg{flex:none;margin-top:2px}',
    '.line.success{--tone:#55dc92}.line.warning{--tone:#f8c952}.line.error{--tone:#ff7272}.line.info{--tone:#62caf4}.line svg{color:var(--tone)}',
    '.line.small{font-size:12px}',
    '.word{display:inline-block;max-width:100%;margin-bottom:12px;padding:2px 8px;border-radius:4px;background:#3b4148;color:#f1f3f5;font-size:15px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.word.deleted{background:transparent;color:#a3abb4;text-decoration:line-through;padding-left:0}.word.wait{background:transparent;color:#a3abb4;padding-left:0}',
    '.chips{display:flex;flex-wrap:wrap;gap:6px}',
    '.chips button{display:inline-flex;align-items:center;gap:4px;height:32px;padding:0 14px;border-radius:999px;border:1px solid #59616b;background:#343a40;color:#f1f3f5;font-family:inherit;font-size:13px;font-weight:500;line-height:1;white-space:nowrap;cursor:pointer;transition:background 120ms,border-color 120ms,color 120ms}',
    '.chips button:hover:not(:disabled){background:#3b4148;border-color:#c1c7ce}.chips button:active:not(:disabled){background:#42484f}',
    '.chips button:disabled{border-color:transparent;color:#868e96;cursor:not-allowed}',
    '.chips button.ghost{background:transparent;border-color:transparent;color:#c1c7ce}.chips button.ghost:hover:not(:disabled){background:#2e3439;border-color:transparent;color:#f1f3f5}',
    '.chips button:focus-visible,.back:focus-visible{outline:2px solid #8fb5ff;outline-offset:2px}',
    'textarea{display:block;width:100%;min-height:84px;resize:vertical;margin-bottom:8px;padding:8px 12px;border-radius:12px;border:1px solid transparent;background:#343a40;color:#f1f3f5;font-family:inherit;font-size:14px;line-height:1.55}',
    'textarea:hover{border-color:#59616b}textarea:focus{border-color:#c1c7ce;outline:none}textarea::placeholder{color:#868e96}',
    '.note{position:fixed;pointer-events:auto;display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:6px;background:#25292d;border:1px solid #3a4148;box-shadow:0 8px 24px rgba(0,0,0,.4);color:#f1f3f5;font-size:12px;font-weight:500;max-width:80vw}',
    '.note[hidden]{display:none}.note svg{flex:none;color:#f8c952}',
    '@keyframes pop{from{opacity:0;transform:translateY(-2px)}}@media (prefers-reduced-motion:reduce){.chip{animation:none}}',
  ].join('');
  const marks = document.createElement('div');
  marks.className = 'marks';
  const chip = document.createElement('section');
  chip.className = 'chip';
  chip.hidden = true;
  chip.setAttribute('role', 'dialog');
  chip.setAttribute('aria-label', 'AI 누리 보안 칩');
  const note = document.createElement('div');
  note.className = 'note';
  note.setAttribute('role', 'status');
  note.hidden = true;
  shadow.append(style, marks, chip, note);
  document.documentElement.append(host);
  const REASONS = [
    ['business_need', '업무상 필요'],
    ['false_positive', '오탐'],
    ['approved', '승인된 개인정보'],
  ];
  // 가는 선 아이콘(lucide 계열, 선 1.5). 페이지의 Trusted Types 정책과 부딪히지 않게 DOM으로 만든다
  const ICONS = {
    success: [['circle', { cx: 12, cy: 12, r: 10 }], ['path', { d: 'm9 12 2 2 4-4' }]],
    warning: [
      ['path', { d: 'm21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3' }],
      ['path', { d: 'M12 9v4' }],
      ['path', { d: 'M12 17h.01' }],
    ],
    error: [['circle', { cx: 12, cy: 12, r: 10 }], ['path', { d: 'm15 9-6 6' }], ['path', { d: 'm9 9 6 6' }]],
    info: [['circle', { cx: 12, cy: 12, r: 10 }], ['path', { d: 'M12 16v-4' }], ['path', { d: 'M12 8h.01' }]],
    back: [['path', { d: 'm15 18-6-6 6-6' }]],
    undo: [['path', { d: 'M9 14 4 9l5-5' }], ['path', { d: 'M4 9h10.5a5.5 5.5 0 0 1 0 11H11' }]],
  };
  function icon(name) {
    const ns = 'http://www.w3.org/2000/svg',
      svg = document.createElementNS(ns, 'svg');
    for (const [k, v] of Object.entries({
      viewBox: '0 0 24 24',
      width: 16,
      height: 16,
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': 1.5,
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'aria-hidden': 'true',
    }))
      svg.setAttribute(k, v);
    for (const [tag, attrs] of ICONS[name]) {
      const part = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) part.setAttribute(k, v);
      svg.append(part);
    }
    return svg;
  }
  let el,
    value = '',
    version = 0,
    checked = -1,
    busy = false,
    hold = 0,
    spans = [],
    paintJob = 0,
    selected = null;
  let generation = 0,
    lastURL = location.href;
  const msg = async (action, body = {}) => {
    const r = await api.runtime.sendMessage({ action, document: documentId, ...body });
    if (!r || r.error) throw new Error(r?.error || 'AI 누리 보안 연결이 끊겼어요.');
    return r.value;
  };
  const read = () => (el ? editor.snapshot(el).text : '');
  const cp = (text) => Array.from(text);
  /* '으로'/'로': 받침이 없거나 ㄹ 받침이면 '로' (숫자는 읽는 소리로) */
  function ro(word) {
    const c = word.replace(/[)\]}>"' ]+$/, '').slice(-1);
    let batchim = false,
      rieul = false;
    if (/[가-힣]/.test(c)) {
      const k = (c.charCodeAt(0) - 0xac00) % 28;
      batchim = k !== 0;
      rieul = k === 8;
    } else if (/[0-9]/.test(c)) {
      batchim = '013678'.includes(c);
      rieul = '178'.includes(c);
    } else if (/[a-z]/i.test(c)) {
      batchim = 'lmnr'.includes(c.toLowerCase());
      rieul = 'lr'.includes(c.toLowerCase());
    }
    return batchim && !rieul ? '으로' : '로';
  }
  const slice = (text, a, b) => cp(text).slice(a, b).join('');
  function close() {
    generation++;
    selected = null;
    chip.hidden = true;
    chip.replaceChildren();
  }
  function report(info) {
    note.replaceChildren();
    if (info) note.append(icon('warning'), document.createTextNode(info));
    note.hidden = !info || !el;
    if (!note.hidden) {
      const box = el.getBoundingClientRect();
      Object.assign(note.style, {
        left: Math.max(4, box.left) + 'px',
        top: Math.max(4, box.top - 32) + 'px',
      });
    }
  }
  function position() {
    if (chip.hidden) return;
    const button =
      selected && !selected.done && [...marks.children].find((b) => b.dataset.start === String(selected.start));
    const panel = chip.getBoundingClientRect();
    const hit = button ? button.getBoundingClientRect() : el?.isConnected ? el.getBoundingClientRect() : null;
    if (!hit) {
      chip.style.left = chip.style.top = '16px';
      return;
    }
    chip.style.left = Math.max(4, Math.min(hit.left, innerWidth - panel.width - 4)) + 'px';
    chip.style.top =
      (hit.bottom + panel.height + 8 <= innerHeight
        ? hit.bottom + 8
        : Math.max(4, hit.top - panel.height - 8)) + 'px';
  }
  function paint() {
    paintJob = 0;
    marks.replaceChildren();
    if (!el?.isConnected || !spans.length) return position();
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
      b.setAttribute('aria-label', box.span.label + ' · 마스킹 또는 원문유지');
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
        if (e.isTrusted) open(box.span);
      };
      marks.append(b);
    }
    position();
  }
  function repaint() {
    if (!paintJob) paintJob = requestAnimationFrame(paint);
  }
  async function pump() {
    if (
      busy ||
      hold ||
      !el ||
      !value.trim() ||
      checked === version ||
      (!document.hasFocus() && document.hidden)
    )
      return;
    busy = true;
    const at = version,
      text = value,
      field = el;
    try {
      const result = await msg('preview', { text });
      if (lastActive && field === el && read() === value) {
        spans = editor.stableRanges(result.spans, text, value);
        report(
          result.unlocated ? '위치를 확정할 수 없는 항목 ' + result.unlocated + '건 · 직접 확인해 주세요' : '',
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
    // 보냈거나 비웠다: 이 입력창의 원문유지·바꾼 값 기억을 지운다 (다음 입력은 다시 묻는다)
    if (el === next && value.trim() && !text.trim()) void msg('reset').catch(() => {});
    el = next;
    value = text;
    version++;
    if (selected && !selected.done) close();
    report('');
    repaint();
    void pump(); // Start on the input event; while busy, only the newest revision is queued.
  }

  // ------------------------------------------------------------------ 칩
  function node(tag, text, parent = chip, className = '') {
    const n = document.createElement(tag);
    if (text) n.textContent = text;
    if (className) n.className = className;
    parent.append(n);
    return n;
  }
  // 칩을 눌러도 입력창의 커서가 그대로 남게 한다 (Tab으로 칩에 가는 키보드 사용은 그대로)
  const keepCaret = (e) => e.preventDefault();
  function line(text, tone = 'info', small = false) {
    const n = node('div', '', chip, 'line ' + tone + (small ? ' small' : ''));
    n.append(icon(tone), document.createTextNode(text));
    return n;
  }
  function stage({ title = '', back = null, notice = '', word = null, deleted = false, wait = false, message = '', tone = 'success', chips = [] }) {
    chip.replaceChildren();
    chip.hidden = false;
    if (title || back) {
      const head = node('div', '', chip, 'head');
      if (back) {
        const b = node('button', '', head, 'back');
        b.type = 'button';
        b.dataset.chip = 'back';
        b.onmousedown = keepCaret;
        b.setAttribute('aria-label', '뒤로');
        b.append(icon('back'));
        b.onclick = (e) => {
          if (e.isTrusted) back();
        };
      }
      node('span', title, head);
    }
    if (notice) line(notice, 'warning', true);
    if (word !== null) node('div', word, chip, 'word' + (deleted ? ' deleted' : '') + (wait ? ' wait' : ''));
    if (message) line(message, tone);
    if (chips.length) {
      const row = node('div', '', chip, 'chips');
      for (const c of chips) {
        const b = node('button', c.text, row, c.ghost ? 'ghost' : '');
        b.type = 'button';
        b.dataset.chip = c.id;
        b.onmousedown = keepCaret;
        if (c.icon) b.prepend(icon(c.icon));
        b.disabled = !c.run;
        b.onclick = async (e) => {
          if (!e.isTrusted || b.disabled) return;
          b.disabled = true;
          try {
            await c.run();
          } catch (err) {
            if (!chip.hidden) line(err.message, 'error');
          } finally {
            if (b.isConnected) b.disabled = false;
            position();
          }
        };
      }
    }
    position();
  }
  function open(span, notice = '') {
    close();
    selected = { ...span, text: value, version, id: generation };
    root(notice);
  }
  const live = (s) => s && s.id === generation && s === selected;
  function root(notice = '') {
    const s = selected;
    stage({
      title: s.label,
      notice,
      message: s.keep === false ? '비밀값·기밀은 원문으로 보낼 수 없어요.' : '',
      tone: 'warning',
      chips: [
        { id: 'mask', text: '마스킹', run: () => mask(false) },
        { id: 'keep', text: '원문유지', run: s.keep === false ? null : reasons },
      ],
    });
  }
  async function mask(every) {
    const s = selected;
    const title = s.label + ' · ' + (every ? '모두 마스킹' : '마스킹');
    stage({ title, back: root, word: '바꿀 값을 만드는 중…', wait: true, chips: [{ id: 'apply', text: '적용' }] });
    let out;
    try {
      out = await msg('mask', { text: s.text, start: s.start, end: s.end, label: s.label, every });
    } catch (err) {
      if (!live(s)) return;
      stage({ title, back: root, message: err.message, tone: 'error', chips: [{ id: 'retry', text: '다시 시도', run: () => mask(every) }] });
      return;
    }
    if (!live(s)) return;
    const others = new Set(spans.map((x) => x.start)).size;
    const chips = [{ id: 'apply', text: '적용' + (out.count > 1 ? ' · ' + out.count + '곳' : ''), run: () => apply(out) }];
    if (!every && others > 1) chips.push({ id: 'all', text: '모두 적용 ' + others, run: () => mask(true) });
    stage({ title, back: root, word: out.deleted ? out.preview : out.word, deleted: out.deleted, chips });
  }
  async function apply(out) {
    const s = selected;
    if (read() !== s.text || s.version !== version) throw new Error('입력이 바뀌었어요. 하이라이트를 다시 눌러 주세요.');
    hold++;
    s.done = true; // 이제 글이 바뀐다: 이 칩은 끝 단계로 남는다
    try {
      await msg('commit', { ticket: out.ticket, keys: out.keys });
      if (!editor.edit(el, s.text, out.edits)) {
        s.done = false;
        await msg('commit', { ticket: out.ticket, keys: out.keys, applied: false }).catch(() => {});
        throw new Error('입력창에서 바꾸지 못했어요. 입력창을 누른 뒤 다시 시도해 주세요.');
      }
    } catch (err) {
      s.done = false;
      throw err;
    } finally {
      hold--;
    }
    changed();
    void pump();
    finish(s, out.deleted ? '지웠어요' : out.word + ro(out.word) + ' 바꿨어요', () => undoMask(s, out));
  }
  function reasons() {
    const s = selected;
    stage({
      title: s.label + ' · 원문유지 사유',
      back: root,
      chips: REASONS.map(([code, name]) => ({ id: 'reason-' + code, text: name, run: () => keep(code) })),
    });
  }
  async function keep(reason) {
    const s = selected;
    if (read() !== s.text) throw new Error('입력이 바뀌었어요. 하이라이트를 다시 눌러 주세요.');
    const out = await msg('keep', { text: s.text, start: s.start, end: s.end, reason });
    const kept = slice(s.text, s.start, s.end);
    spans = spans.filter((x) => slice(value, x.start, x.end) !== kept);
    s.done = true;
    repaint();
    finish(s, '원문을 유지해요 · ' + out.reason, async () => {
      await msg('unkeep', { text: s.text, start: s.start, end: s.end });
      checked = -1;
      close();
      void pump();
    });
  }
  function finish(s, message, undo) {
    if (s !== selected) return;
    const chips = [];
    if (spans.length) chips.push({ id: 'next', text: '다음 항목', run: () => open(spans[0]) });
    chips.push({ id: 'undo', text: '되돌리기', ghost: true, icon: 'undo', run: undo });
    stage({ title: s.label, message, tone: 'success', chips });
  }
  async function undoMask(s, out) {
    const text = read();
    const back = await msg('undo', { ticket: out.ticket, keys: out.keys, text });
    hold++;
    try {
      await msg('commit', { ticket: out.ticket, keys: out.keys, applied: false });
      if (!editor.edit(el, text, back.edits)) {
        await msg('commit', { ticket: out.ticket, keys: out.keys }).catch(() => {});
        throw new Error('되돌리지 못했어요. 입력이 바뀌었는지 확인해 주세요.');
      }
    } finally {
      hold--;
    }
    close();
    changed();
    void pump();
  }
  function blocked() {
    if (!chip.hidden) {
      position();
      return;
    }
    if (spans.length) open(spans[0], '보내기 전에 표시된 항목을 처리해 주세요');
  }
  // 답변 복원은 확장 아이콘에서 직접 열 때만 (하이라이트 칩에는 두지 않는다)
  function restorePanel() {
    close();
    selected = { done: true, id: generation };
    stage({ title: 'AI 답변 복원' });
    const input = node('textarea');
    input.rows = 3;
    input.placeholder = 'AI 답변을 붙여넣으세요';
    input.setAttribute('aria-label', '복원할 AI 답변');
    const row = node('div', '', chip, 'chips');
    const run = node('button', '복원', row);
    run.type = 'button';
    run.dataset.chip = 'restore';
    run.onclick = async (e) => {
      if (!e.isTrusted || !input.value.trim()) return;
      run.disabled = true;
      try {
        const out = await msg('restore', { text: input.value });
        input.value = out.text;
        input.readOnly = true;
        input.select();
        run.remove();
      } catch (err) {
        line(err.message, 'error');
        run.disabled = false;
      }
      position();
    };
    position();
  }
  api.runtime.onMessage.addListener((message) => {
    if (message.action === 'openRestore') restorePanel();
  });

  // ------------------------------------------------------------------ Enter·전송 막기
  const inEditor = (target) => el && (target === el || el.contains(target));
  function isSend(button) {
    if (!el || !button || button.disabled) return false;
    const name = [button.getAttribute('aria-label'), button.getAttribute('data-testid'), button.id]
      .filter(Boolean)
      .join(' ');
    if (/send|submit|보내기|전송/i.test(name)) {
      let n = el;
      for (let i = 0; i < 8 && n; i++, n = n.parentElement) if (n.contains(button)) return true;
      return false;
    }
    return button.type === 'submit' && !!button.form && button.form.contains(el);
  }
  window.addEventListener(
    'keydown',
    (e) => {
      if (e.key === 'Escape' && !chip.hidden) {
        close();
        return;
      }
      if (e.key !== 'Enter' || e.shiftKey || e.isComposing || e.keyCode === 229) return;
      if (!inEditor(e.target) || !spans.length) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      blocked();
    },
    true,
  );
  window.addEventListener(
    'click',
    (e) => {
      const button = e.target instanceof Element ? e.target.closest('button') : null;
      if (!spans.length || !isSend(button)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      blocked();
    },
    true,
  );
  window.addEventListener(
    'submit',
    (e) => {
      if (spans.length && el && e.target instanceof Element && e.target.contains(el)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        blocked();
      }
    },
    true,
  );
  document.addEventListener(
    'pointerdown',
    (e) => {
      if (!e.composedPath().includes(host)) close();
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
          report('AI 누리 보안 감시 일시정지');
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
    repaint();
    if (changes.key.newValue) void pump();
    else report('AI 누리 보안 연결을 해제했어요.');
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) void pump();
  });
  window.addEventListener('pageshow', changed);
  window.addEventListener('pagehide', () => {
    el = null;
    value = '';
    version++;
    close();
    spans = [];
    marks.replaceChildren();
  });
  changed();
})();

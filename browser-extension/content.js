(() => {
  'use strict';
  // The page may be replaced during client-side navigation. Recheck without
  // reading, persisting, or sending the user's prompt.
  const host = document.createElement('div');
  host.setAttribute('data-kpguard-status', '');
  const shadow = host.attachShadow({ mode: 'closed' });
  const status = document.createElement('div');
  status.setAttribute('role', 'status');
  Object.assign(status.style, {
    position: 'fixed', right: '12px', bottom: '12px', zIndex: '2147483647',
    padding: '8px 12px', borderRadius: '8px', background: '#1f2937',
    color: '#fff', font: '12px system-ui, sans-serif',
    boxShadow: '0 2px 8px #0004', pointerEvents: 'none'
  });
  shadow.append(status);
  let last = null;
  function refresh() {
    if (!host.isConnected && document.body) document.body.append(host);
    const composer = document.querySelector('#prompt-textarea');
    const found = Boolean(composer && (composer.matches('textarea') || composer.isContentEditable));
    if (found !== last) {
      status.textContent = found ? 'KP Guard: 입력창 감지 · 검사는 아직 연결되지 않음' : 'KP Guard: 입력창을 찾는 중';
      last = found;
    }
  }
  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; refresh(); });
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  refresh();
})();

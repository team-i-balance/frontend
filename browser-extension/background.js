/* The pairing key is used only by extension pages and this worker. */
const api = globalThis.browser || chrome;
const base = 'http://127.0.0.1:47681/v1/';
const enc = new TextEncoder();
const actions = new Set([
  'status',
  'preview',
  'inspect',
  'selection',
  'decide',
  'restore',
  'files',
  'settings',
  'server_tools',
  'detail',
  'desktop_restore',
]);
const trusted = new Set(['https://claude.ai']);
const hex = (bytes) => Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, '0')).join('');
if (api.storage.local.setAccessLevel) api.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });

async function post(path, json, headers = {}) {
  const response = await fetch(base + path, {
    method: 'POST',
    body: json,
    cache: 'no-store',
    credentials: 'omit',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
    headers: { 'Content-Type': 'application/json', ...headers },
    signal: AbortSignal.timeout(35000),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'KP Guard 연결을 확인해 주세요.');
  return data;
}

async function call(message, sender) {
  const extensionPage = sender.url === api.runtime.getURL('connect.html');
  if (
    sender.id !== api.runtime.id ||
    !actions.has(message.action) ||
    !(extensionPage
      ? ['status', 'server_tools', 'settings', 'files'].includes(message.action)
      : sender.tab && sender.frameId === 0 && trusted.has(new URL(sender.url).origin))
  ) {
    throw new Error('허용되지 않은 요청입니다.');
  }
  const { key } = await api.storage.local.get('key');
  if (!key) throw new Error('확장 아이콘에서 KP Guard 연결 코드를 입력해 주세요.');
  const hmac = await crypto.subtle.importKey(
    'raw',
    enc.encode(key),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const sign = async (value) => hex(await crypto.subtle.sign('HMAC', hmac, enc.encode(value)));
  const nonce = hex(crypto.getRandomValues(new Uint8Array(16)));
  const { challenge, proof } = await post('challenge', JSON.stringify({ nonce }));
  if (!/^[a-f0-9]{32}$/.test(challenge) || proof !== (await sign('server\n' + nonce + '\n' + challenge))) {
    throw new Error('연결 코드가 일치하지 않습니다.');
  }
  const body = JSON.stringify({
    ...message,
    site: !extensionPage && sender.tab.active ? new URL(sender.url).hostname : null,
    channel: String(sender.tab?.id ?? 'settings') + '-' + String(message.document || '').slice(0, 64),
  });
  return post('action', body, {
    'X-KP-Challenge': challenge,
    'X-KP-Proof': await sign('client\n' + challenge + '\n' + body),
  });
}
api.runtime.onMessage.addListener((message, sender, respond) => {
  call(message, sender).then(
    (value) => respond({ value }),
    (error) => {
      const note =
        error instanceof TypeError || error.name === 'TimeoutError'
          ? 'KP Guard 연결을 확인해 주세요. PC 앱과 팀 서버가 응답해야 합니다.'
          : error.message;
      respond({ error: note });
    },
  );
  return true;
});

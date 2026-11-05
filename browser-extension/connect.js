const api = globalThis.browser || chrome;
const input = document.querySelector('input'),
  out = document.querySelector('output');
function say(text, tone = '') {
  out.textContent = text;
  out.dataset.tone = tone;
}
async function status() {
  try {
    const r = await api.runtime.sendMessage({ action: 'status' });
    if (r.error) say(r.error, 'error');
    else if (r.value.active) say('연결됨 · ' + (r.value.mode === 'server' ? '팀 서버 검사' : '이 PC에서 검사'), 'ok');
    else say('감시 일시정지');
  } catch {
    say('PC 앱 연결을 확인해 주세요.', 'error');
  }
}
document.querySelector('form').onsubmit = async (e) => {
  e.preventDefault();
  await api.storage.local.set({ key: input.value.trim() });
  input.value = '';
  await status();
};
document.querySelector('#remove').onclick = async () => {
  await api.storage.local.remove('key');
  say('연결을 해제했어요.');
};
status();
document.querySelector('#restore').onclick = async () => {
  try {
    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    await api.tabs.sendMessage(tab.id, { action: 'openRestore' });
    window.close();
  } catch {
    say('Claude(claude.ai) 탭에서 여세요.', 'error');
  }
};

document.querySelector('#tools').onclick = async () => {
  const r = await api.runtime.sendMessage({ action: 'server_tools' });
  if (r.error) say(r.error, 'error');
  else say('PC 앱에서 서버 기능을 열었어요.', 'ok');
};

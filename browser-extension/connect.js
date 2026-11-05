const api = globalThis.browser || chrome;
const input = document.querySelector('input'),
  out = document.querySelector('output');
async function status() {
  try {
    const r = await api.runtime.sendMessage({ action: 'status' });
    out.textContent =
      r.error ||
      (r.value.active
        ? '연결됨 · ' + (r.value.mode === 'server' ? '팀 서버 검사' : '이 PC에서 검사')
        : '감시 일시정지');
  } catch {
    out.textContent = 'PC 앱 연결을 확인해 주세요.';
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
  out.textContent = '연결을 해제했습니다.';
};
status();
document.querySelector('#restore').onclick = async () => {
  try {
    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    await api.tabs.sendMessage(tab.id, { action: 'openRestore' });
    window.close();
  } catch {
    out.textContent = 'ChatGPT 또는 Claude 탭에서 여세요.';
  }
};

document.querySelector('#tools').onclick = async () => {
  const r = await api.runtime.sendMessage({ action: 'server_tools' });
  out.textContent = r.error || 'PC 앱에서 서버 기능을 열었습니다.';
};

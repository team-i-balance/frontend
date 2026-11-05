"""시스템 트레이 상주 (pystray). 설치돼 있지 않으면 App이 상태 창 + 단축키로 대체한다.

pystray 콜백은 트레이 스레드에서 불리므로 tkinter를 직접 만지지 않고 App.post()로 큐에 넣는다.
"""
from __future__ import annotations

import threading

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # 선택 의존성
    pystray = None

from .samples import SAMPLES

# 디자인 토큰의 상태색: 감시 중 = 성공(green/300), 일시정지 = 경고(amber/300), 꺼짐 = 비활성(gray/400)
_COLORS = {"on": "#5BD38A", "paused": "#FFC55C", "off": "#6F727C"}


def available() -> bool:
    return pystray is not None


def _image(state: str):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(32, 4), (58, 14), (54, 42), (32, 60), (10, 42), (6, 14)], fill=_COLORS[state])
    d.rectangle([22, 28, 42, 46], fill="#131416")
    d.arc([24, 16, 40, 36], 180, 360, fill="#131416", width=4)
    return img


class Tray:
    def __init__(self, app):
        self.app = app
        post = app.post
        sample_items = [pystray.MenuItem(name, (lambda n: lambda: post("sample", n))(name)) for name in SAMPLES]
        menu = pystray.Menu(
            pystray.MenuItem("프롬프트 점검 창 열기  (Ctrl+Alt+P)", lambda: post("review"), default=True),
            pystray.MenuItem("답변 복원 창  (Ctrl+Alt+R)", lambda: post("restore")),
            pystray.MenuItem("파일 검사 · 엑셀 보호본  (Ctrl+Alt+F)", lambda: post("files")),
            pystray.MenuItem("예시로 점검해보기", pystray.Menu(*sample_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("감시 켜기", lambda: post("toggle"), checked=lambda item: app.settings["monitoring"]),
            pystray.MenuItem("30분 일시정지", lambda: post("pause"), checked=lambda item: app.is_paused()),
            pystray.MenuItem("설정·상태  (Ctrl+Alt+O)", lambda: post("settings")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", lambda: post("quit")),
        )
        self.icon = pystray.Icon("KPGuard", _image(app.state()), self._title(), menu)

    def _title(self) -> str:
        return {"on": "KP Guard · 감시 중", "paused": "KP Guard · 일시정지", "off": "KP Guard · 꺼짐"}[self.app.state()]

    def start(self) -> None:
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()

    def refresh(self) -> None:
        self.icon.icon = _image(self.app.state())
        self.icon.title = self._title()
        self.icon.update_menu()

    def notify(self, message: str) -> None:
        try:
            self.icon.notify(message, "KP Guard")
        except Exception:
            pass

    def stop(self) -> None:
        self.icon.stop()

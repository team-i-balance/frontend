"""백그라운드 상주 앱: 트레이·단축키·클립보드 감시 → 점검 창/답변 복원 창.

스레드 규칙: tkinter는 메인 스레드에서만 다룬다. 트레이·단축키 스레드는 post()로 큐에 넣기만 한다.
"""
from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import messagebox

from .. import config
from ..detect.pipeline import detect
from ..detect.scoring import AUTO_APPLY
from ..events.log import EventLog
from ..sanitize.engine import Session
from ..sanitize.restore import restore
from ..system import autostart, win32
from . import theme, tray
from .restore_window import RestoreWindow
from .review import ReviewWindow
from .samples import SAMPLES
from .settings_window import SettingsWindow

HOTKEYS = {
    1: (win32.MOD_CONTROL | win32.MOD_ALT, ord("P"), "Ctrl+Alt+P 점검 창"),
    2: (win32.MOD_CONTROL | win32.MOD_ALT, ord("R"), "Ctrl+Alt+R 답변 복원"),
    3: (win32.MOD_CONTROL | win32.MOD_ALT, ord("O"), "Ctrl+Alt+O 설정"),
}
PAUSE_SECONDS = 30 * 60
MAX_CLIP_LEN = 50_000


class App:
    def __init__(self, background: bool = False):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("KP Guard")
        theme.setup(self.root)

        self.settings = config.load_settings()
        self.base = config.load_base()
        self.session = Session(self.base)
        self.ignore: set[str] = set()   # 오탐 등록 값 (메모리)
        self.log = EventLog(config.data_dir())
        self.paused_until = 0.0
        self._q: queue.Queue = queue.Queue()
        self.reviews: list[ReviewWindow] = []
        self.restore_win: RestoreWindow | None = None
        self.settings_win: SettingsWindow | None = None
        self._last_seq = win32.clipboard_sequence()
        self._own_seq = None
        self._toast_win = None

        self.tray = tray.Tray(self) if tray.available() else None
        self.hotkeys = win32.HotkeyThread(HOTKEYS, lambda hid: self.post("hotkey", hid))
        self.hotkeys.start()
        if self.tray:
            self.tray.start()

        self.root.after(100, self._poll)
        self.root.after(700, self._watch_clipboard)
        self.root.after(400, lambda: self._first_run(background))

    def run(self) -> None:
        self.root.mainloop()

    # ================================================================== 상태
    def is_paused(self) -> bool:
        return time.time() < self.paused_until

    def state(self) -> str:
        if not self.settings["monitoring"]:
            return "off"
        return "paused" if self.is_paused() else "on"

    def monitoring_active(self) -> bool:
        return self.state() == "on"

    def save_settings(self) -> None:
        config.save_settings(self.settings)

    def _state_changed(self) -> None:
        if self.tray:
            self.tray.refresh()
        if self.settings_win:
            self.settings_win.refresh()

    # ================================================================== 큐 (다른 스레드 → 메인)
    def post(self, cmd: str, *args) -> None:
        self._q.put((cmd, args))

    def _poll(self) -> None:
        try:
            while True:
                cmd, args = self._q.get_nowait()
                self._dispatch(cmd, *args)
        except queue.Empty:
            pass
        if self.is_paused() is False and self.paused_until:
            self.paused_until = 0.0
            self._state_changed()
        self.root.after(100, self._poll)

    def _dispatch(self, cmd: str, *args) -> None:
        if cmd == "review":
            self.open_review("", "manual")
        elif cmd == "restore":
            self.open_restore()
        elif cmd == "settings":
            self.open_settings()
        elif cmd == "sample":
            self.open_review(SAMPLES[args[0]], "sample")
        elif cmd == "toggle":
            self.settings["monitoring"] = not self.settings["monitoring"]
            self.save_settings()
            self.toast("감시를 켰어요." if self.settings["monitoring"] else "감시를 껐어요. 복사한 내용을 점검하지 않아요.")
            self._state_changed()
        elif cmd == "pause":
            if self.is_paused():
                self.paused_until = 0.0
                self.toast("일시정지를 해제했어요.")
            else:
                self.paused_until = time.time() + PAUSE_SECONDS
                self.toast("30분 동안 감시를 멈춰요.")
            self._state_changed()
        elif cmd == "hotkey":
            {1: lambda: self.open_review("", "hotkey"), 2: self.open_restore, 3: self.open_settings}[args[0]]()
        elif cmd == "quit":
            self.quit()

    # ================================================================== 클립보드 감시
    def _watch_clipboard(self) -> None:
        try:
            seq = win32.clipboard_sequence()
            if seq != self._last_seq:
                self._last_seq = seq
                if (self.settings["clipboard_watch"] and self.monitoring_active() and seq != self._own_seq
                        and not win32.own_window_in_foreground()):
                    text = win32.get_clipboard_text()
                    if text:
                        self._inspect_clipboard(text)
        except Exception as exc:  # 감시 루프는 죽지 않게
            print("clipboard watch error:", exc)
        finally:
            self.root.after(700, self._watch_clipboard)

    def _inspect_clipboard(self, text: str) -> None:
        if not self.settings["min_length"] <= len(text) <= MAX_CLIP_LEN:
            return
        # AI 답변을 복사한 경우: 가짜 값이 들어 있으면 복원 창
        if self.session.entries and restore(text, self.session.entries)[1]:
            self.open_restore(text)
            return
        det = detect(text, self.base, self.ignore)
        if det.blockers or any(s.confidence >= AUTO_APPLY for s in det.spans):
            self.open_review(text, "clipboard")

    def copy_to_clipboard(self, text: str) -> None:
        if not win32.set_clipboard_text(text):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
        self._own_seq = win32.clipboard_sequence()
        self._last_seq = self._own_seq

    # ================================================================== 창 열기
    def open_review(self, text: str = "", source: str = "manual") -> None:
        if not text and self.reviews:  # 빈 창 요청이면 기존 창을 앞으로
            self.reviews[-1]._bring_front()
            return
        self.reviews.append(ReviewWindow(self, text, source))

    def review_closed(self, win) -> None:
        if win in self.reviews:
            self.reviews.remove(win)

    def open_restore(self, text: str = "") -> None:
        if self.restore_win:
            if text:
                self.restore_win.src.delete("1.0", "end")
                self.restore_win.src.insert("1.0", text)
            self.restore_win.lift()
            self.restore_win.focus_force()
            return
        self.restore_win = RestoreWindow(self, text)

    def restore_closed(self, _win) -> None:
        self.restore_win = None

    def open_settings(self) -> None:
        if self.settings_win:
            self.settings_win.deiconify()
            self.settings_win.lift()
            self.settings_win.focus_force()
            return
        self.settings_win = SettingsWindow(self)

    def settings_closed(self) -> None:
        self.settings_win = None

    # ================================================================== 알림
    def toast(self, message: str, ms: int = 3500) -> None:
        if self._toast_win is not None:
            self._toast_win.destroy()
        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)
        win.attributes("-topmost", True)
        tk.Label(win, text=message, bg="#111827", fg="white", font=theme.FONT, padx=14, pady=10,
                 wraplength=360, justify="left").pack()
        win.update_idletasks()
        x = win.winfo_screenwidth() - win.winfo_width() - 24
        y = win.winfo_screenheight() - win.winfo_height() - 72
        win.geometry(f"+{x}+{y}")
        self._toast_win = win
        win.after(ms, lambda: self._close_toast(win))

    def _close_toast(self, win) -> None:
        if self._toast_win is win:
            self._toast_win = None
        win.destroy()

    # ================================================================== 시작·종료
    def _first_run(self, background: bool) -> None:
        if not self.tray or not background:
            self.open_settings()
        if not self.settings["autostart_asked"]:
            self.settings["autostart_asked"] = True
            self.save_settings()
            ok = messagebox.askyesno(
                "KP Guard",
                "Windows에 로그인할 때 KP Guard를 자동으로 실행할까요?\n\n"
                "백그라운드에서 복사한 프롬프트를 점검합니다. 설정 창에서 언제든 끌 수 있어요.",
                parent=self.settings_win or self.root)
            if ok:
                try:
                    autostart.enable()
                except OSError as exc:
                    messagebox.showerror("KP Guard", f"자동 시작 등록 실패: {exc}")
            if self.settings_win:
                self.settings_win.v_auto.set(autostart.is_enabled())
        if background and self.tray:
            self.tray.notify("백그라운드에서 실행 중이에요. 프롬프트를 복사하면 자동으로 점검해요.")

    def quit(self) -> None:
        self.hotkeys.stop()
        if self.tray:
            self.tray.stop()
        try:
            self.log.export_jsonl()
        finally:
            self.log.close()
            self.root.destroy()

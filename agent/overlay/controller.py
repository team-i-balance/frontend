"""Native overlay lifecycle. Accessibility and network I/O stay off Tk's main thread."""

import sys
import threading
import tkinter as tk
from contextlib import nullcontext
from dataclasses import dataclass

from ..gui import theme
from .live import LiveRanges
from .source import OWN_WINDOW


@dataclass(frozen=True)
class Mark:
    bounds: tuple
    start: int
    end: int
    label: str


@dataclass(frozen=True)
class Frame:
    key: tuple = ()
    text: str = ""
    marks: tuple = ()

    def hit(self, x, y):
        return next((m for m in self.marks if contains(m.bounds, x, y)), None)


def contains(box, x, y):
    return box[0] <= x < box[2] and box[1] <= y < box[3]


def make_frame(source, spans):
    marks = []
    for span in spans:
        a, b = span["start"], span["end"]
        if not 0 <= a < b <= len(source.draft.text):
            continue
        for box in source.rectangles(a, b):
            marks.append(Mark(box, a, b, span["label"]))
    return Frame(source.key, source.draft.text, tuple(marks))


class NativeOverlay:
    def __init__(self, app):
        self.app = app
        self.stop = threading.Event()
        self.live = LiveRanges(self._preview)
        self.frame = self.shown = Frame()
        self.chip = None
        self.stage = "initializing"
        self.pressed = False
        self.press_at = None
        self.paint = None
        self.status = "Windows·macOS에서 지원합니다."
        if sys.platform not in ("win32", "darwin"):
            return
        try:
            if sys.platform == "win32":
                from . import windows as platform
            else:
                from . import macos as platform
            self.paint = platform.Paint()
        except (ImportError, OSError, RuntimeError):
            self.status = "화면 연동을 불러오지 못했습니다. 앱 의존성을 설치해 주세요."
            return
        self.status = "입력창 대기 중"
        threading.Thread(target=self.live.run, daemon=True, name="overlay-preview").start()
        threading.Thread(
            target=self._read, args=(platform.Reader,), daemon=True, name="overlay-accessibility"
        ).start()
        self.timer = app.root.after(25, self._tick)

    def enabled(self):
        return self.app.settings.get("native_overlay", True) and self.app.monitoring_active()

    def _preview(self, text):
        if self.stop.is_set() or not self.enabled():
            return {"spans": []}
        return self.app.browser_actions.preview(text)

    def _read(self, reader_class):
        try:
            with reader_class() as reader:
                while not self.stop.is_set():
                    try:
                        with getattr(reader, "pool", nullcontext)():
                            delay = self._sample(reader)
                    except PermissionError:
                        self.clear()
                        self.status, delay = "손쉬운 사용 권한이 필요합니다.", 0.8
                    except Exception:
                        # Windows/tabs can disappear during a cross-process call. Never log a draft.
                        reader.reset()
                        self.clear()
                        self.status, delay = "입력창 좌표 대기 중", 0.12
                    self.stop.wait(delay)
        except Exception:
            self.clear()
            self.status = "운영체제 화면 연동을 시작하지 못했습니다."

    def clear(self):
        self.live.update(None)
        self.frame = Frame()

    def _sample(self, reader):
        if not self.enabled():
            reader.reset()
            self.clear()
            self.status = "꺼짐"
            return 0.15
        source = reader.sample()
        self.stage = getattr(reader, "reason", "accessibility")
        if source is OWN_WINDOW and self.chip is not None:
            return 0.05
        if source is None or source is OWN_WINDOW:
            self.clear()
            self.status = "입력창 대기 중"
            return 0.09
        text = source.draft.text
        if self.app.browser_actions.extension_covers(source.key[1], text):
            self.clear()
            self.status = "이 입력창은 연결된 확장에서 표시 중"
            return 0.08
        key = (source.key, self.app.server_mode(), id(self.app.server), self.app.browser_actions.generation)
        spans = self.live.update(key, text)
        self.frame = make_frame(source, spans)
        self.status = "검사 연결을 확인해 주세요." if self.live.error else "입력 중 실시간 검사"
        return 0.04

    def _tick(self):
        try:
            frame = self.frame if self.enabled() else Frame()
            if frame != self.shown:
                if self.chip and (frame.key, frame.text) != (self.shown.key, self.shown.text):
                    self._dismiss()
                if frame.marks != self.shown.marks:
                    self.paint.draw([m.bounds for m in frame.marks])
                self.shown = frame
            if frame.marks or self.chip or self.pressed:
                self._mouse(frame, *self.paint.pointer())
        finally:
            if not self.stop.is_set():
                self.timer = self.app.root.after(16 if self.shown.marks or self.chip else 50, self._tick)

    def _mouse(self, frame, x, y, down):
        if down and not self.pressed:
            if self.chip:
                w = self.chip
                left, top = w.winfo_rootx(), w.winfo_rooty()
                if contains((left, top, left + w.winfo_width(), top + w.winfo_height()), x, y):
                    self.press_at = None
                    self.pressed = True
                    return
                self._dismiss()
            mark = frame.hit(x, y)
            self.press_at = (frame.key, frame.text, mark.start, mark.end) if mark else None
        elif not down and self.pressed:
            mark = frame.hit(x, y)
            if mark and self.press_at == (frame.key, frame.text, mark.start, mark.end):
                self._open(frame, mark, x, y)
            self.press_at = None
        self.pressed = down

    def _open(self, frame, mark, x, y):
        self._dismiss()
        window = self.chip = tk.Toplevel(self.app.root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=theme.BG_RAISED, highlightthickness=1, highlightbackground=theme.AMBER_500)
        theme.label(window, mark.label, "caption12", theme.AMBER_300).pack(anchor="w", padx=10, pady=(6, 2))

        def action(callback):
            def invoke():
                self._dismiss()
                if self.enabled():
                    callback()

            return invoke

        options = [
            (
                "이 항목 가명화·원문 유지",
                lambda: self.app.open_review(frame.text, "overlay", selected=(mark.start, mark.end)),
            ),
            ("프롬프트 전체 점검", lambda: self.app.open_review(frame.text, "overlay")),
            ("답변 복원", self.app.open_restore),
            ("파일 검사", self.app.open_files),
            ("서버 기능 전체", lambda: self.app.post("server_tools")),
        ]
        for label, callback in options:
            theme.Button(window, label, action(callback), size="sm").pack(fill="x", padx=5, pady=2)
        window.bind("<Escape>", lambda _: self._dismiss())
        window.update_idletasks()
        # Keep the chip on the monitor containing the word, including monitors left of the primary.
        left, top, right, bottom = self.paint.screen_at(x, y)
        width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        px = max(left, min(x, right - width))
        py = y + 8 if y + 8 + height <= bottom else max(top, y - height - 8)
        window.geometry(f"+{px}+{py}")
        window.deiconify()
        window.lift()

    def _dismiss(self):
        if self.chip is not None:
            self.chip.destroy()
            self.chip = None

    def close(self):
        self.stop.set()
        self.live.close()
        if self.paint:
            self.app.root.after_cancel(self.timer)
            self._dismiss()
            self.paint.close()

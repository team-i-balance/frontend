"""Native overlay lifecycle. Accessibility and network I/O stay off Tk's main thread."""

import sys
import threading
from contextlib import nullcontext
from dataclasses import dataclass

from ..gui import theme
from ..gui.review import REASONS
from .chips import Cascade, Menu, Option, TkSurface
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
    area: tuple | None = None

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
    return Frame(source.key, source.draft.text, tuple(marks), getattr(source, "area", None))


class NativeOverlay:
    def __init__(self, app):
        self.app = app
        self.stop = threading.Event()
        self.live = LiveRanges(self._preview)
        self.frame = self.shown = Frame()
        self.chip = None
        self.fading = []
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
            self._animate()
        finally:
            if not self.stop.is_set():
                busy = self.shown.marks or self.chip or self.fading
                self.timer = self.app.root.after(16 if busy else 50, self._tick)

    def _animate(self):
        for cascade in [c for c in (self.chip, *self.fading) if c is not None]:
            action = cascade.step()
            if action is None:
                try:
                    cascade.present()
                    continue
                except Exception:  # a window that cannot be drawn is removed without running anything
                    action = lambda: None  # noqa: E731
            # The closing animation ended: remove the chips, then run the final choice.
            if cascade is self.chip:
                self.chip = None
            if cascade in self.fading:
                self.fading.remove(cascade)
            cascade.surface.close()
            if self.enabled():
                action()

    def _mouse(self, frame, x, y, down):
        if self.chip:
            self.chip.move(x, y)
        if down and not self.pressed:
            if self.chip:
                if self.chip.contains(x, y):
                    self.press_at = None
                    self.pressed = True
                    self.chip.press_at(x, y)
                    return
                same = self.chip.span
                self._dismiss()
                mark = frame.hit(x, y)
                if mark and (frame.key, frame.text, mark.start, mark.end) == same:
                    mark = None  # a second click on the same word only closes its chips
            else:
                mark = frame.hit(x, y)
            self.press_at = (frame.key, frame.text, mark.start, mark.end) if mark else None
        elif not down and self.pressed:
            if self.chip and self.press_at is None:
                self.chip.release_at(x, y)
            mark = frame.hit(x, y)
            if mark and self.press_at == (frame.key, frame.text, mark.start, mark.end):
                self._open(frame, mark, x, y)
            self.press_at = None
        self.pressed = down

    def _menu(self, frame, mark):
        app, span = self.app, (mark.start, mark.end)
        value = frame.text[mark.start : mark.end]

        def review(choice=None):
            return lambda: app.open_review(frame.text, "overlay", selected=span, choice=choice)

        def ignore():
            app.ignore.add(value)
            self.clear()  # check the prompt again without this value
            toast = getattr(app, "toast", None)
            if toast:
                toast("이 값은 앞으로 표시하지 않아요")

        reasons = Menu("원문 유지 사유", [Option(text, review(("keep", code))) for code, text in REASONS])
        item = Menu(
            f"{mark.label} 처리",
            [
                Option("가린 채로 보내기", review(("mask", None))),
                Option("원문 그대로 보내기", menu=reasons),
                Option("오탐 등록 · 다시 표시 안 함", ignore),
            ],
        )
        more = Menu(
            "다른 기능",
            [
                Option("답변 복원", app.open_restore),
                Option("파일 검사", app.open_files),
                Option("서버 기능 전체", lambda: app.post("server_tools")),
            ],
        )
        return Menu(
            mark.label,
            [
                Option("이 항목 처리", menu=item),
                Option("프롬프트 전체 점검", lambda: app.open_review(frame.text, "overlay")),
                Option("다른 기능", menu=more),
            ],
            note=theme.masked(value),
        )

    def _open(self, frame, mark, x, y):
        self._dismiss()
        make = getattr(self.paint, "chip_surface", None)
        surface = make() if make else TkSurface(self.app.root)
        # The whole word, also when it wraps onto a second line.
        parts = [m.bounds for m in frame.marks if (m.start, m.end) == (mark.start, mark.end)] or [mark.bounds]
        anchor = min(p[0] for p in parts), min(p[1] for p in parts), max(p[2] for p in parts), max(p[3] for p in parts)
        scale = self.app.root.winfo_fpixels("1i") / 96
        chip = Cascade(self._menu(frame, mark), anchor, frame.area, self.paint.screen_at(x, y), scale, surface)
        chip.span = (frame.key, frame.text, mark.start, mark.end)
        self.chip = chip
        chip.step()
        chip.present()

    def _dismiss(self):
        if self.chip is not None:
            self.chip.close()
            self.fading.append(self.chip)
            self.chip = None

    def close(self):
        self.stop.set()
        self.live.close()
        if self.paint:
            self.app.root.after_cancel(self.timer)
            for cascade in [c for c in (self.chip, *self.fading) if c is not None]:
                cascade.surface.close()
            self.chip, self.fading = None, []
            self.paint.close()

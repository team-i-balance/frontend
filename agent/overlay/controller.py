"""원래 입력창 오버레이: 하이라이트·칩·Enter 막기. 손쉬운 사용(UIA/AX)과 네트워크는 Tk 메인 스레드 밖에서 한다.

칩 흐름 (별도 창 없이 칩을 펼쳐 마무리):
  하이라이트 클릭 → 마스킹 / 원문유지
    마스킹 → 바뀔 값만 보여 주고 적용 / 모두 적용 → 입력창의 그 자리만 바꾼다 → 끝(다음 항목·되돌리기)
    원문유지 → 업무상 필요 / 오탐 / 승인된 개인정보 → 끝(다음 항목·되돌리기)
  하이라이트가 남아 있으면 그 입력창의 Enter(전송)를 막고 첫 항목의 칩을 연다.
"""

import queue
import sys
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass

from ..browser.actions import KEEP_REASONS, ChipError
from ..sanitize.josa import ro as _ro
from ..gui.chip import Chip, ChipPanel
from ..server.client import ServerError
from .live import LiveRanges
from .source import OWN_WINDOW


@dataclass(frozen=True)
class Mark:
    bounds: tuple
    start: int
    end: int
    label: str
    keep: bool = True


@dataclass(frozen=True)
class Frame:
    key: tuple = ()
    text: str = ""
    marks: tuple = ()
    spans: tuple = ()      # 남은 하이라이트 전부 (화면 밖 포함): (start, end, label, keep)
    bounds: tuple = ()     # 입력창 화면 영역

    def hit(self, x, y):
        return next((m for m in self.marks if contains(m.bounds, x, y)), None)


def contains(box, x, y):
    return box[0] <= x < box[2] and box[1] <= y < box[3]


def make_frame(source, spans):
    marks, kept = [], []
    for span in spans:
        a, b = span["start"], span["end"]
        if not 0 <= a < b <= len(source.draft.text):
            continue
        keep = span.get("keep", True) is not False
        kept.append((a, b, span["label"], keep))
        for box in source.rectangles(a, b):
            marks.append(Mark(box, a, b, span["label"], keep))
    return Frame(source.key, source.draft.text, tuple(marks), tuple(kept), getattr(source, "bounds", ()) or ())


def channel_of(key):
    return "native:%x" % (hash(key) & 0xFFFFFFFFFFFF)


class NativeOverlay:
    def __init__(self, app):
        self.app = app
        self.stop = threading.Event()
        self.live = LiveRanges(self._preview)
        self.frame = self.shown = Frame()
        self.chip = None          # ChipPanel
        self.chip_text = None     # 칩이 가리키는 (key, text). 글이 바뀌면 그 칩은 닫는다 (끝 단계는 None)
        self.target = None        # Enter를 막을 입력창 (Windows hwnd / macOS pid). 하이라이트가 없으면 None
        self.edits = queue.Queue()
        self.had_text = {}
        self.stage = "initializing"
        self.pressed = False
        self.press_at = None
        self.paint = None
        self.guard = None
        self.platform = None
        self.status = "Windows·macOS에서 지원합니다."
        if sys.platform not in ("win32", "darwin"):
            return
        try:
            if sys.platform == "win32":
                from . import windows as platform
            else:
                from . import macos as platform
            self.paint = platform.Paint()
            self.platform = platform
        except (ImportError, OSError, RuntimeError):
            self.status = "화면 연동을 불러오지 못했습니다. 앱 의존성을 설치해 주세요."
            return
        self.status = "입력창 대기 중"
        try:
            self.guard = platform.EnterGuard(lambda: self.target, self._enter_blocked)
            self.guard.start()
        except Exception:  # 권한이 없거나 훅을 걸지 못하면 Enter는 막지 못한다 (하이라이트·칩은 동작)
            self.guard = None
        threading.Thread(target=self.live.run, daemon=True, name="overlay-preview").start()
        threading.Thread(
            target=self._read, args=(platform.Reader,), daemon=True, name="overlay-accessibility"
        ).start()
        self.timer = app.root.after(25, self._tick)

    def enabled(self):
        return self.app.settings.get("native_overlay", True) and self.app.monitoring_active()

    def _preview(self, text, key=None):
        if self.stop.is_set() or not self.enabled():
            return {"spans": []}
        channel = channel_of(key[0]) if key else None
        return self.app.browser_actions.preview(text, channel)

    # ------------------------------------------------------------------ 손쉬운 사용 스레드
    def _read(self, reader_class):
        try:
            with reader_class() as reader:
                while not self.stop.is_set():
                    try:
                        with getattr(reader, "pool", nullcontext)():
                            self._run_edits(reader)
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
        self.target = None

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
        channel = channel_of(source.key)
        if not text.strip():
            if self.had_text.pop(channel, False):
                self.app.browser_actions.reset(channel)  # 보냈거나 비웠다: 다음 입력은 다시 묻는다
        else:
            self.had_text[channel] = True
        if self.app.browser_actions.extension_covers(source.key[1], text):
            self.clear()
            self.status = "이 입력창은 연결된 확장에서 표시 중"
            return 0.08
        key = (source.key, self.app.server_mode(), id(self.app.server), self.app.browser_actions.generation)
        spans = self.live.update(key, text)
        self.frame = make_frame(source, spans)
        # Enter(전송)는 하이라이트가 남은 그 입력창에 커서가 있을 때만 막는다 (주소창 등의 Enter는 그대로)
        self.target = source.key[0] if spans and source.focused else None
        self.status = "검사 연결을 확인해 주세요." if self.live.error else "입력 중 실시간 검사"
        return 0.04

    def _run_edits(self, reader):
        """칩에서 고른 바꾸기를 입력창에 친다. 뒤에서부터 한 자리씩, 매번 다시 읽어 확인한다."""
        while True:
            try:
                key, text, changes, done = self.edits.get_nowait()
            except queue.Empty:
                return
            ok = False
            try:
                ok = self._edit(reader, key, text, changes)
            except Exception:
                ok = False
            self.app.post("call", done, ok)

    def _edit(self, reader, key, text, changes):
        focus = getattr(reader, "focus", None)
        if focus is not None:
            focus(key)
        expected = text
        for a, b, new in sorted(changes, key=lambda c: c[0], reverse=True):
            source = self._fresh(reader, key, expected)
            if source is None or source.replace is None or not source.replace(a, b, new):
                return False
            expected = expected[:a] + new + expected[b:]
        return self._fresh(reader, key, expected, wait=0.6) is not None

    def _fresh(self, reader, key, text, wait=0.3):
        """입력창이 그 글이 될 때까지 잠깐 기다리며 다시 읽는다."""
        until = time.monotonic() + wait
        while True:
            source = reader.sample()
            if source not in (None, OWN_WINDOW) and source.key == key and source.draft.text == text:
                return source
            if time.monotonic() >= until:
                return None
            time.sleep(0.03)

    # ------------------------------------------------------------------ Tk 메인 스레드
    def _tick(self):
        try:
            frame = self.frame if self.enabled() else Frame()
            if frame != self.shown:
                if self.chip and self.chip_text and (frame.key, frame.text) != self.chip_text:
                    self._dismiss()
                if frame.marks != self.shown.marks:
                    self.paint.draw([m.bounds for m in frame.marks])
                self.shown = frame
            if self.chip is not None and not self.chip.exists():
                self.chip = None
            if frame.marks or self.chip or self.pressed:
                self._mouse(frame, *self.paint.pointer())
        finally:
            if not self.stop.is_set():
                self.timer = self.app.root.after(16 if self.shown.marks or self.chip else 50, self._tick)

    def _mouse(self, frame, x, y, down):
        if down and not self.pressed:
            if self.chip:
                if self.chip.contains(x, y):
                    self.press_at = None
                    self.pressed = True
                    return
                self._dismiss()
            mark = frame.hit(x, y)
            self.press_at = (frame.key, frame.text, mark.start, mark.end) if mark else None
        elif not down and self.pressed:
            mark = frame.hit(x, y)
            if mark and self.press_at == (frame.key, frame.text, mark.start, mark.end):
                self._open(frame, mark)
            self.press_at = None
        self.pressed = down

    def _panel(self, frame, mark):
        x, y, _, bottom = mark.bounds if mark else (frame.bounds or (0, 0, 0, 0))
        top = y
        if not mark and frame.bounds:
            x, top, _, bottom = frame.bounds
        self._dismiss()
        screen = self.paint.screen_at(x, bottom)
        give_back = getattr(self.platform, "give_back", None)
        target = frame.key[0] if frame.key else None
        self.chip = ChipPanel(self.app.root, x, bottom, screen, below=top,
                              no_activate=getattr(self.platform, "no_activate", None),
                              on_show=(lambda: give_back(target)) if give_back and target else None)
        return self.chip

    def _open(self, frame, mark, note=""):
        """첫 칩: 마스킹 / 원문유지 두 개."""
        span = (mark.start, mark.end, mark.label, mark.keep)
        panel = self._panel(frame, mark)
        self.chip_text = (frame.key, frame.text)
        self._root(panel, frame, span, note)

    def _root(self, panel, frame, span, note=""):
        start, end, label, keep = span
        panel.show(title=label, note=note,
                   message="" if keep else "비밀값·기밀은 원문으로 보낼 수 없어요.", tone="warning",
                   chips=[Chip("마스킹", lambda: self._mask(panel, frame, span)),
                          Chip("원문유지", lambda: self._keep_reasons(panel, frame, span), enabled=keep)])

    def _back(self, panel, frame, span):
        return lambda: self._root(panel, frame, span)

    def _work(self, job, done):
        """네트워크가 필요한 일은 작업 스레드에서. done(결과, 오류 문구)은 메인 스레드에서."""
        def run():
            try:
                result, error = job(), None
            except (ChipError, ServerError) as exc:
                result, error = None, str(exc)
            except Exception:  # 예외 메시지에 원문이 섞일 수 있어 보여 주지 않는다
                result, error = None, "처리하지 못했어요. 다시 눌러 주세요."
            self.app.post("call", done, result, error)

        threading.Thread(target=run, daemon=True, name="overlay-chip").start()

    def _mask(self, panel, frame, span, every=False):
        """마스킹: 바뀔 값만 보여 주고 아래 칩으로 적용을 고른다."""
        start, end, label, _ = span
        channel = channel_of(frame.key)
        back = self._back(panel, frame, span)
        panel.show(title=f"{label} · 마스킹", back=back, word="바꿀 값을 만드는 중…",
                   chips=[Chip("적용", None, enabled=False)])
        actions = self.app.browser_actions

        def done(result, error):
            if self.chip is not panel or not panel.exists():
                return
            if error:
                panel.show(title=f"{label} · 마스킹", back=back, message=error, tone="error",
                           chips=[Chip("다시 시도", lambda: self._mask(panel, frame, span, every))])
                return
            others = len({(a, b) for a, b, *_ in frame.spans}) - 1
            word = result["preview"] if result["deleted"] else result["word"]
            chips = [Chip("적용" + (f" · {result['count']}곳" if result["count"] > 1 else ""),
                          lambda: self._apply(panel, frame, span, result))]
            if others > 0 and not every:
                chips.append(Chip(f"모두 적용 {others + 1}", lambda: self._mask(panel, frame, span, every=True)))
            panel.show(title=f"{label} · " + ("모두 마스킹" if every else "마스킹"), back=back, word=word,
                       deleted=result["deleted"], chips=chips)

        self._work(lambda: actions.mask(channel, frame.text, start, end, label, every=every), done)

    def _apply(self, panel, frame, span, result):
        """적용: 입력창의 그 자리만 바꾼다 (새 창 없이). 바꾸기 전에 기록 상태를 먼저 맞춘다."""
        channel = channel_of(frame.key)
        actions = self.app.browser_actions
        label = span[2]
        try:
            actions.commit(channel, result["ticket"], result["keys"])
        except ChipError as exc:
            panel.show(title=label, message=str(exc), tone="error")
            return
        self.chip_text = None  # 이제 글이 바뀐다: 이 칩은 끝 단계로 남는다
        panel.show(title=label, message="입력창에서 바꾸는 중…", tone="info")

        def done(ok):
            if ok:
                word = "지웠어요" if result["deleted"] else f"{result['word']}{_ro(result['word'])} 바꿨어요"
                self._finish(panel, frame.key, label, word, lambda: self._undo_mask(panel, frame.key, label, result))
            else:
                actions.commit(channel, result["ticket"], result["keys"], applied=False)
                if self.chip is panel and panel.exists():
                    panel.show(title=label, message="입력창에서 바꾸지 못했어요. 입력창을 누른 뒤 다시 시도해 주세요.",
                               tone="error")

        self.edits.put((frame.key, frame.text, result["edits"], done))

    def _keep_reasons(self, panel, frame, span):
        """원문유지: 사유 칩 셋."""
        label = span[2]
        panel.show(title=f"{label} · 원문유지 사유", back=self._back(panel, frame, span),
                   chips=[Chip(name, lambda code=code: self._keep(panel, frame, span, code))
                          for code, name in KEEP_REASONS.items()])

    def _keep(self, panel, frame, span, reason):
        start, end, label, _ = span
        channel = channel_of(frame.key)
        actions = self.app.browser_actions
        try:
            out = actions.keep(channel, frame.text, start, end, reason)
        except ChipError as exc:
            panel.show(title=label, back=self._back(panel, frame, span), message=str(exc), tone="error")
            return
        self.chip_text = None
        self.live.drop(frame.text[start:end])
        self._drop(frame, start, end)

        def undo():
            try:
                actions.unkeep(channel, frame.text, start, end)
            except ChipError as exc:
                panel.show(title=label, message=str(exc), tone="error")
                return
            self.live.refresh()  # 같은 글을 다시 검사해 하이라이트를 되살린다
            self._dismiss()

        self._finish(panel, frame.key, label, f"원문을 유지해요 · {out['reason']}", undo)

    def _drop(self, frame, start, end):
        """원문유지한 값의 하이라이트를 바로 지운다 (다음 검사를 기다리지 않음)."""
        value = frame.text[start:end]
        spans = [s for s in frame.spans if frame.text[s[0]:s[1]] != value]
        marks = tuple(m for m in frame.marks if frame.text[m.start:m.end] != value)
        self.frame = Frame(frame.key, frame.text, marks, tuple(spans), frame.bounds)
        if not spans:
            self.target = None

    def _finish(self, panel, key, label, message, undo):
        """끝 단계: 결과 한 줄 + 다음 항목 / 되돌리기."""
        if self.chip is not panel or not panel.exists():
            return
        chips = []
        if self.frame.key == key and self.frame.marks:
            chips.append(Chip("다음 항목", self._next))
        chips.append(Chip("되돌리기", undo, kind="ghost", icon="undo"))
        panel.show(title=label, message=message, tone="success", chips=chips)

    def _next(self):
        frame = self.frame
        if frame.marks:
            self._open(frame, frame.marks[0])
        else:
            self._dismiss()

    def _undo_mask(self, panel, key, label, result):
        channel = channel_of(key)
        actions = self.app.browser_actions
        frame = self.frame
        if frame.key != key:
            panel.show(title=label, message="입력창을 다시 눌러 주세요.", tone="error")
            return
        try:
            back = actions.undo(channel, result["ticket"], result["keys"], frame.text)
        except ChipError as exc:
            panel.show(title=label, message=str(exc), tone="error")
            return
        actions.commit(channel, result["ticket"], result["keys"], applied=False)

        def done(ok):
            if not ok:
                actions.commit(channel, result["ticket"], result["keys"])
                if self.chip is panel and panel.exists():
                    panel.show(title=label, message="되돌리지 못했어요. 입력이 바뀌었는지 확인해 주세요.", tone="error")
            elif self.chip is panel:
                self._dismiss()

        self.edits.put((key, frame.text, back["edits"], done))

    def _enter_blocked(self):
        """Enter 훅 스레드에서 불린다: 메인 스레드에 칩을 띄우라고만 알린다."""
        self.app.post("call", self._show_blocked)

    def _show_blocked(self):
        frame = self.frame
        if self.chip is not None and self.chip.exists():
            self.chip.win.lift()
            return
        note = "보내기 전에 표시된 항목을 처리해 주세요"
        if frame.marks:
            self._open(frame, frame.marks[0], note)
        elif frame.spans:
            a, b, label, keep = frame.spans[0]
            panel = self._panel(frame, None)
            self.chip_text = (frame.key, frame.text)
            self._root(panel, frame, (a, b, label, keep), note)

    def _dismiss(self):
        if self.chip is not None:
            self.chip.destroy()
            self.chip = None
        self.chip_text = None

    def close(self):
        self.stop.set()
        self.live.close()
        if self.guard is not None:
            try:
                self.guard.stop()
            except Exception:
                pass
        if self.paint:
            self.app.root.after_cancel(self.timer)
            self._dismiss()
            self.paint.close()

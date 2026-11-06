"""원래 입력창 오버레이: 하이라이트·칩·Enter 막기. 손쉬운 사용(UIA/AX)과 네트워크는 Tk 메인 스레드 밖에서 한다.

칩 흐름 (별도 창 없이 칩을 펼쳐 마무리). 칩은 단계형(chips.Cascade): 고른 칩 옆에 다음 칩이 열리고 앞 칩은 남는다.
  하이라이트 클릭 → 마스킹 / 원문유지
    마스킹 → 바뀔 값만 보여 주고 적용 / 모두 적용 → (칩이 모두 닫힌 뒤) 입력창의 그 자리만 바꾼다
    원문유지 → 업무상 필요 / 오탐 / 승인된 개인정보
  최종 선택 뒤에는 결과 칩 하나(다음 항목 · 되돌리기)가 뜬다.
  하이라이트가 남아 있으면 그 입력창의 Enter(전송)를 막고 첫 항목의 칩을 연다.
읽는 곳: 브라우저의 Claude(claude.ai)·Codex(chatgpt.com/codex), 터미널의 Claude Code·Codex CLI 프롬프트.
터미널 프롬프트는 커서를 옮길 수 없어 마지막 줄을 Backspace로 지우고 다시 입력한다 (source.retype).
"""

import queue
import sys
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass

from ..browser.actions import KEEP_REASONS, ChipError
from ..sanitize.josa import ro as _ro
from .chips import Cascade, Menu, Option, TkSurface
from ..server.client import ServerError
from .live import LiveRanges
from .source import OWN_WINDOW, TARGETS, TERMINALS, retype


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


def _edit_failed(key):
    if len(key) > 1 and key[1] in TERMINALS:
        return "터미널에서는 커서가 입력 끝에 있을 때 마지막 줄의 값만 바꿀 수 있어요."
    return "입력창에서 바꾸지 못했어요. 입력창을 누른 뒤 다시 시도해 주세요."


class NativeOverlay:
    def __init__(self, app):
        self.app = app
        self.stop = threading.Event()
        self.live = LiveRanges(self._preview)
        self.frame = self.shown = Frame()
        self.chip = None          # chips.Cascade
        self.fading = []          # cascades playing their closing animation
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
        return self._check(text, key[0] if key else None)

    def _check(self, text, source=None):
        """한 입력창의 글을 검사한다. source(읽은 입력창의 키)의 종류로 결정이 가는 AI를 정한다."""
        if source is None:
            return self.app.browser_actions.preview(text)
        return self.app.browser_actions.preview(text, channel_of(source), target=TARGETS.get(source[1]))

    # ------------------------------------------------------------------ 손쉬운 사용 스레드
    def _read(self, reader_class):
        for attempt in range(4):
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
                return
            except Exception:
                # Starting UI Automation can fail for a moment (e.g. while another thread loads it first): retry.
                self.clear()
                self.status = "운영체제 화면 연동을 시작하지 못했습니다."
                if self.stop.wait(1.0 + attempt):
                    return

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
        if key[1] in TERMINALS:
            return self._retype(reader, key, text, changes)
        expected = text
        for a, b, new in sorted(changes, key=lambda c: c[0], reverse=True):
            source = self._fresh(reader, key, expected)
            if source is None or source.replace is None or not source.replace(a, b, new):
                return False
            expected = expected[:a] + new + expected[b:]
        return self._fresh(reader, key, expected, wait=0.6) is not None

    def _retype(self, reader, key, text, changes):
        """터미널 프롬프트: 커서가 입력 끝에 있을 때 마지막 줄을 지우고 다시 입력한다."""
        source = self._fresh(reader, key, text)
        if source is None or source.keys is None:
            return False

        def read():
            now = reader.sample()
            return now.draft.text if now not in (None, OWN_WINDOW) and now.key == key else None

        expected = retype(read, source.keys, text, changes)
        return expected is not None and self._fresh(reader, key, expected, wait=0.8) is not None

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
                    # 터미널의 표시는 클릭을 직접 받는다 (콘솔이 선택 모드로 멈추지 않게)
                    terminal = len(frame.key) > 1 and frame.key[1] in TERMINALS
                    self.paint.draw([m.bounds for m in frame.marks], solid=terminal)
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
            if action is not None:
                self._finished(cascade, action)
                continue
            try:
                cascade.present()
            except Exception:  # a window that cannot be drawn is removed without running anything
                self._finished(cascade, None)

    def _finished(self, cascade, action):
        """The closing animation ended: remove the chips, then run the final choice."""
        if cascade is self.chip:
            self.chip, self.chip_text = None, None
        if cascade in self.fading:
            self.fading.remove(cascade)
        try:
            cascade.surface.close()
        except Exception:
            pass
        if action is not None and self.enabled():
            action()

    def _mouse(self, frame, x, y, down):
        if self.chip:
            self.chip.move(x, y)
        if down and not self.pressed:
            mark = None
            if self.chip:
                if self.chip.contains(x, y):
                    self.press_at = None
                    self.pressed = True
                    self.chip.press_at(x, y)
                    return
                same = getattr(self.chip, "span", None)
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
                self._open(frame, mark)
            self.press_at = None
        self.pressed = down

    def _cascade(self, frame, mark, menu, ring=True):
        """Chips for one word (or the prompt box when the word is off screen)."""
        self._dismiss()
        if mark is not None:
            parts = [m.bounds for m in frame.marks if (m.start, m.end) == (mark.start, mark.end)] or [mark.bounds]
            anchor = (min(p[0] for p in parts), min(p[1] for p in parts),
                      max(p[2] for p in parts), max(p[3] for p in parts))
        else:
            box = frame.bounds or (0, 0, 40, 20)
            anchor = (box[0], box[1], box[0] + min(80, box[2] - box[0]), box[1] + 20)
            ring = False
        make = getattr(self.paint, "chip_surface", None)
        surface = make() if make else TkSurface(self.app.root)
        try:
            scale = self.app.root.winfo_fpixels("1i") / 96
        except Exception:
            scale = 1.0
        screen = self.paint.screen_at((anchor[0] + anchor[2]) // 2, anchor[3])
        cascade = Cascade(menu, anchor, frame.bounds or None, screen, scale, surface, ring=ring)
        cascade.on_finished = self._finished
        cascade.span = (frame.key, frame.text, mark.start, mark.end) if mark is not None else None
        self.chip = cascade
        if not make:  # a Tk window may take focus (macOS): give it back to the editor
            give_back = getattr(self.platform, "give_back", None)
            if give_back and frame.key:
                try:
                    give_back(frame.key[0])
                except Exception:
                    pass
        cascade.step()
        try:
            cascade.present()
        except Exception:
            pass
        return cascade

    def _open(self, frame, mark, note=""):
        """첫 칩: 마스킹 / 원문유지 두 개."""
        span = (mark.start, mark.end, mark.label, mark.keep)
        self._cascade(frame, mark, self._root(frame, span, note))
        self.chip_text = (frame.key, frame.text)

    def _root(self, frame, span, note=""):
        start, end, label, keep = span
        lines = []
        if note:
            lines.append((note, "warning"))
        if not keep:
            lines.append(("비밀값·기밀은 원문으로 보낼 수 없어요.", "warning"))
        return Menu(label, [
            Option("마스킹", open=lambda depth: self._mask(frame, span, depth)),
            Option("원문유지", menu=self._reasons(frame, span), enabled=keep),
        ], lines=tuple(lines))

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

    def _mask(self, frame, span, depth, every=False):
        """마스킹: 바뀔 값만 보여 주고 아래 칩으로 적용을 고른다. 값을 만드는 동안은 불러오는 칩."""
        start, end, label, _ = span
        channel = channel_of(frame.key)
        title = "모두 마스킹" if every else "마스킹"
        cascade = self.chip
        actions = self.app.browser_actions

        def done(result, error):
            if self.chip is not cascade or cascade is None or not cascade.exists():
                return
            if error:
                menu = Menu(title, [Option("다시 시도", stay=lambda: self._retry(cascade, frame, span, depth, every))],
                            lines=((error, "error"),))
            else:
                others = len({(a, b) for a, b, *_ in frame.spans}) - 1
                word = result["preview"] if result["deleted"] else result["word"]
                options = [Option("적용" + (f" · {result['count']}곳" if result["count"] > 1 else ""),
                                  lambda: self._apply(frame, span, result))]
                if others > 0 and not every:
                    options.append(Option(f"모두 적용 {others + 1}",
                                          open=lambda d: self._mask(frame, span, d, every=True)))
                menu = Menu(title, options, lines=((word, "deleted" if result["deleted"] else "word"),))
            cascade.replace(depth, menu)

        self._work(lambda: actions.mask(channel, frame.text, start, end, label, every=every), done)
        return Menu(title, [Option("적용", enabled=False)], lines=(("바꿀 값을 만드는 중…", "info"),))

    def _retry(self, cascade, frame, span, depth, every):
        cascade.replace(depth, self._mask(frame, span, depth, every))

    def _reasons(self, frame, span):
        """원문유지: 사유 칩 셋."""
        return Menu("원문유지 사유", [Option(name, lambda code=code: self._keep(frame, span, code))
                                    for code, name in KEEP_REASONS.items()])

    def _result(self, frame, span, message, tone, options=()):
        """최종 선택 뒤: 결과 한 줄 + 다음 항목 / 되돌리기 (글이 바뀌어도 남는다)."""
        start, end, label, keep = span
        mark = next((m for m in self.shown.marks if (m.start, m.end) == (start, end)), None)
        if mark is None:
            anchor_frame = Frame(frame.key, frame.text, (), (), frame.bounds)
            cascade = self._cascade(anchor_frame, None, Menu(label, list(options), lines=((message, tone),)))
        else:
            cascade = self._cascade(frame, mark, Menu(label, list(options), lines=((message, tone),)), ring=False)
        self.chip_text = None
        return cascade

    def _apply(self, frame, span, result):
        """적용: 입력창의 그 자리만 바꾼다 (새 창 없이). 바꾸기 전에 기록 상태를 먼저 맞춘다."""
        channel = channel_of(frame.key)
        actions = self.app.browser_actions
        try:
            actions.commit(channel, result["ticket"], result["keys"])
        except ChipError as exc:
            self._result(frame, span, str(exc), "error")
            return
        self._result(frame, span, "입력창에서 바꾸는 중…", "info")

        def done(ok):
            if ok:
                word = "지웠어요" if result["deleted"] else f"{result['word']}{_ro(result['word'])} 바꿨어요"
                self._finish(frame, span, word, lambda: self._undo_mask(frame, span, result))
            else:
                actions.commit(channel, result["ticket"], result["keys"], applied=False)
                self._result(frame, span, _edit_failed(frame.key), "error")

        self.edits.put((frame.key, frame.text, result["edits"], done))

    def _keep(self, frame, span, reason):
        start, end, label, _ = span
        channel = channel_of(frame.key)
        actions = self.app.browser_actions
        try:
            out = actions.keep(channel, frame.text, start, end, reason)
        except ChipError as exc:
            self._result(frame, span, str(exc), "error")
            return
        self.live.drop(frame.text[start:end])
        self._drop(frame, start, end)

        def undo():
            try:
                actions.unkeep(channel, frame.text, start, end)
            except ChipError as exc:
                self._result(frame, span, str(exc), "error")
                return
            self.live.refresh()  # 같은 글을 다시 검사해 하이라이트를 되살린다

        self._finish(frame, span, f"원문을 유지해요 · {out['reason']}", undo)

    def _drop(self, frame, start, end):
        """원문유지한 값의 하이라이트를 바로 지운다 (다음 검사를 기다리지 않음)."""
        value = frame.text[start:end]
        spans = [s for s in frame.spans if frame.text[s[0]:s[1]] != value]
        marks = tuple(m for m in frame.marks if frame.text[m.start:m.end] != value)
        self.frame = Frame(frame.key, frame.text, marks, tuple(spans), frame.bounds)
        if not spans:
            self.target = None

    def _finish(self, frame, span, message, undo):
        """끝 단계: 결과 한 줄 + 다음 항목 / 되돌리기."""
        options = []
        if self.frame.key == frame.key and self.frame.marks:
            options.append(Option("다음 항목", self._next))
        options.append(Option("되돌리기", undo))
        self._result(frame, span, message, "success", options)

    def _next(self):
        frame = self.frame
        if frame.marks:
            self._open(frame, frame.marks[0])

    def _undo_mask(self, frame, span, result):
        key = frame.key
        channel = channel_of(key)
        actions = self.app.browser_actions
        now = self.frame
        if now.key != key:
            self._result(frame, span, "입력창을 다시 눌러 주세요.", "error")
            return
        try:
            back = actions.undo(channel, result["ticket"], result["keys"], now.text)
        except ChipError as exc:
            self._result(frame, span, str(exc), "error")
            return
        actions.commit(channel, result["ticket"], result["keys"], applied=False)

        def done(ok):
            if not ok:
                actions.commit(channel, result["ticket"], result["keys"])
                self._result(frame, span, "되돌리지 못했어요. " + (
                    "터미널에서는 커서가 입력 끝에 있어야 해요." if key[1] in TERMINALS
                    else "입력이 바뀌었는지 확인해 주세요."), "error")

        self.edits.put((key, now.text, back["edits"], done))

    def _enter_blocked(self):
        """Enter 훅 스레드에서 불린다: 메인 스레드에 칩을 띄우라고만 알린다."""
        self.app.post("call", self._show_blocked)

    def _show_blocked(self):
        frame = self.frame
        if self.chip is not None and self.chip.exists():
            return
        note = "보내기 전에 표시된 항목을 처리해 주세요"
        if frame.marks:
            self._open(frame, frame.marks[0], note)
        elif frame.spans:
            a, b, label, keep = frame.spans[0]
            self._cascade(frame, None, self._root(frame, (a, b, label, keep), note))
            self.chip_text = (frame.key, frame.text)

    def _dismiss(self):
        if self.chip is not None:
            self.chip.close()
            self.fading.append(self.chip)
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
            for cascade in [c for c in (self.chip, *self.fading) if c is not None]:
                try:
                    cascade.surface.close()
                except Exception:
                    pass
            self.chip, self.fading = None, []
            self.paint.close()

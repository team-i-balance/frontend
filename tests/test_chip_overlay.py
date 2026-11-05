"""원래 입력창 칩: 마스킹 / 원문유지 → 바뀔 값·사유 칩 → 입력창의 그 자리만 바꾼다. 새 창은 열지 않는다.

이 파일 이름은 test_native_overlay보다 앞에 오게 둔다: macOS에서는 Tk가 NSApplication을 먼저 만들어야 한다."""

import queue
import sys
import time
import tkinter as tk
import unittest
from unittest.mock import Mock

from agent import config
from agent.browser.actions import BrowserActions
from agent.gui import theme
from agent.overlay.controller import NativeOverlay, channel_of, make_frame
from agent.overlay.source import Draft, Snapshot, end_only
from agent.server.client import DecideResult, ProcessResult

PHONE = "010-2345-6789"
KEY = (77, "chatgpt.com", (1, 2), "https://chatgpt.com/")


class FakeApp:
    def __init__(self, root):
        self.root = root
        self.base = config.load_base()
        self.ignore = set()
        self.server = Mock()
        self.server_mode = Mock(return_value=True)
        self.monitoring_active = Mock(return_value=True)
        self.detector = Mock(return_value=None)
        self.settings = {"native_overlay": True}
        self.calls = queue.Queue()
        self.browser_actions = BrowserActions(self)

    def post(self, cmd, *args):
        self.calls.put((cmd, args))

    def pump(self, until, limit=3.0):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            try:
                cmd, args = self.calls.get(timeout=0.01)
                if cmd == "call":
                    args[0](*args[1:])
            except queue.Empty:
                pass
            self.root.update()
            if until():
                return True
        return False


class FakeReader:
    def __init__(self, text):
        self.text = text

    @staticmethod
    def focus(key):
        return True

    def sample(self):
        def replace(a, b, new):
            self.text = self.text[:a] + new + self.text[b:]
            return True

        return Snapshot(KEY, Draft.plain(self.text), lambda a, b: [], replace)


class OverlayChipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform == "darwin":
            # Tk needs its own NSApplication. The app always starts Tk first; a test that made one with PyObjC can't.
            try:
                import AppKit

                current = AppKit.NSApp() if callable(getattr(AppKit, "NSApp", None)) else None
            except ImportError:
                current = None
            if current is not None and type(current).__name__ != "TKApplication":
                raise unittest.SkipTest("NSApplication already exists without Tk in this process")
        try:
            cls.root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display for Tk")
        cls.root.withdraw()
        theme.setup(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.app = FakeApp(self.root)
        self.server = self.app.server
        self.server.preview.side_effect = lambda sent: (
            [{"start": sent.find(PHONE), "end": sent.find(PHONE) + len(PHONE), "type": "PHONE"}] if PHONE in sent
            else [], False)
        self.server.process.side_effect = lambda sent: ProcessResult(
            "w1", sent.replace(PHONE, "[전화번호_1]"), ["PHONE"], 1, "S", "high", 0, False)
        self.server.decide.return_value = DecideResult(None, True, "pre_warning")
        self.overlay = NativeOverlay(self.app)
        self.live = self.overlay.live
        # Windows·macOS start the real reader thread, ticker and Enter guard. These tests drive the chip directly.
        self.overlay.stop.set()
        self.addCleanup(self.stop_overlay)
        if self.overlay.paint is not None:
            self.overlay.paint.close()  # frees the Windows window class for the platform tests that follow
        self.overlay.paint = Mock(screen_at=lambda x, y: (0, 0, 1600, 1000), pointer=lambda: (0, 0, False))
        self.overlay.platform = None

    def stop_overlay(self):
        overlay = self.overlay
        self.live.close()
        if overlay.guard is not None:
            overlay.guard.stop()
        if hasattr(overlay, "timer"):
            self.root.after_cancel(overlay.timer)
        overlay._dismiss()

    def frame(self, text):
        time.sleep(0.05)  # let a just-stopped reader thread finish its last pass before the test sets the frame
        spans = self.app.browser_actions.preview(text, channel_of(KEY))["spans"]
        snap = Snapshot(KEY, Draft.plain(text), lambda a, b: [(100 + a * 8, 100, 100 + b * 8, 120)])
        frame = make_frame(snap, spans)
        self.overlay.frame = frame
        return frame

    def test_first_chip_has_only_mask_and_keep(self):
        frame = self.frame(f"고객 {PHONE} 연락")
        self.overlay._open(frame, frame.marks[0])
        self.assertEqual(self.overlay.chip.chip_labels(), ["마스킹", "원문유지"])

    def test_keep_offers_three_reasons_and_finishes_in_the_chip(self):
        text = f"고객 {PHONE} 연락"
        frame = self.frame(text)
        self.overlay._open(frame, frame.marks[0])
        self.overlay.chip.press("원문유지")
        self.assertEqual(self.overlay.chip.chip_labels(), ["업무상 필요", "오탐", "승인된 개인정보"])
        self.overlay.chip.press("승인된 개인정보")
        self.assertIn("되돌리기", self.overlay.chip.chip_labels())
        self.assertTrue(any("승인된 개인정보" in t for t in self.overlay.chip.texts()))
        self.assertEqual(self.overlay.frame.marks, ())
        self.assertIsNone(self.overlay.target)  # 남은 하이라이트가 없으니 Enter를 막지 않는다
        self.assertTrue(self.app.pump(lambda: self.server.decide.called))
        self.assertEqual(self.server.decide.call_args.args[1:], ("send_as_is", "approved"))

    def test_mask_shows_only_the_word_then_edits_in_place(self):
        text = f"고객 {PHONE} 연락"
        frame = self.frame(text)
        self.overlay._open(frame, frame.marks[0])
        self.overlay.chip.press("마스킹")
        self.assertTrue(self.app.pump(lambda: "적용" in self.overlay.chip.chip_labels()))
        self.assertIn("[전화번호_1]", self.overlay.chip.texts())
        self.assertNotIn(text, self.overlay.chip.texts())  # 프롬프트 전체가 아니라 바뀔 값만
        self.overlay.chip.press("적용")
        key, before, edits, done = self.overlay.edits.get_nowait()
        self.assertEqual((key, before), (KEY, text))
        reader = FakeReader(text)
        self.assertTrue(self.overlay._edit(reader, key, before, edits))
        self.assertEqual(reader.text, "고객 [전화번호_1] 연락")
        done(True)
        self.assertIn("되돌리기", self.overlay.chip.chip_labels())
        self.assertTrue(any("[전화번호_1]" in t for t in self.overlay.chip.texts()))
        self.assertEqual(self.app.browser_actions.preview(reader.text, channel_of(KEY))["spans"], [])
        self.assertTrue(self.app.pump(lambda: self.server.decide.called))
        self.assertEqual(self.server.decide.call_args.args[1:], ("send_transformed", None))

    def test_edit_refuses_a_changed_editor(self):
        reader = FakeReader("다른 글")
        self.assertFalse(self.overlay._edit(reader, KEY, f"고객 {PHONE}", [[3, 16, "[x]"]]))
        self.assertEqual(reader.text, "다른 글")

    def test_enter_on_highlight_opens_the_first_chip(self):
        frame = self.frame(f"고객 {PHONE} 연락")
        self.overlay._show_blocked()
        self.assertEqual(self.overlay.chip.chip_labels(), ["마스킹", "원문유지"])
        self.assertTrue(any("보내기 전에" in t for t in self.overlay.chip.texts()))
        self.assertEqual(frame.spans[0][2], "전화번호")

    def test_enter_is_guarded_only_while_typing_in_the_composer(self):
        text = f"고객 {PHONE}"
        span = {"start": 3, "end": 16, "label": "전화번호", "keep": True}

        class Reader:
            reason = "test"

            def __init__(self, focused):
                self.focused = focused

            def sample(self):
                return Snapshot(KEY, Draft.plain(text), lambda a, b: [], None, (), self.focused)

            def reset(self):
                pass

        self.overlay.live = Mock(update=lambda key, text="": [span], error=False)
        self.overlay._sample(Reader(False))  # 주소창 등 다른 곳에 커서: Enter를 막지 않는다
        self.assertIsNone(self.overlay.target)
        self.overlay._sample(Reader(True))
        self.assertEqual(self.overlay.target, KEY[0])
        self.overlay.live = Mock(update=lambda key, text="": [], error=False)
        self.overlay._sample(Reader(True))  # 하이라이트가 없어지면 막지 않는다
        self.assertIsNone(self.overlay.target)

    def test_terminal_only_retypes_the_last_value(self):
        draft = Draft.plain(f"연락 {PHONE}")
        self.assertTrue(end_only(draft, 3, len(draft.text)))
        self.assertFalse(end_only(draft, 0, 2))


if __name__ == "__main__":
    unittest.main()

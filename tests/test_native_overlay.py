"""Live desktop scanning, token coordinates and click handling, independent of a login."""

import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agent.browser.actions import BrowserActions
from agent.overlay.controller import Frame, NativeOverlay, make_frame
from agent.overlay.live import LiveRanges
from agent.overlay.source import Draft, Snapshot, clip, composer, site, terminal_draft


class DraftTests(unittest.TestCase):
    def test_trusted_editors(self):
        self.assertEqual(site("https://chatgpt.com/c/123"), "chatgpt.com")
        self.assertEqual(site("https://claude.ai/new"), "claude.ai")
        for url in (
            "https://chatgpt.com.evil.test",
            "https://claude.ai@evil.test",
            "http://chatgpt.com",
            "https://chatgpt.com:7777/x",
            "file:///chatgpt.com",
        ):
            self.assertFalse(site(url))
        self.assertTrue(composer("prompt-textarea", "", "", False))
        self.assertTrue(composer("", "tiptap ProseMirror", "", True))
        self.assertFalse(composer("search", "", "Search chats", True))

    def test_claude_only_draft_never_scrollback(self):
        raw = "previous reply: PRIVATE\r\n─────────────────\r\n❯ 한글 🧪 010\r\n  다음 줄\r\n─────────────────\r\n  ? for shortcuts\r\n"
        draft = terminal_draft(raw)
        self.assertEqual(draft.text, "한글 🧪 010\n다음 줄")
        for token in ("한글", "🧪", "010", "다음 줄"):
            offset = draft.text.index(token)
            self.assertEqual("".join(raw[a:b] for a, b in draft.parts(offset, offset + len(token))), token)
        self.assertEqual([raw[a:b] for a, b in draft.parts(5, len(draft.text))], ["010", "다음 줄"])
        self.assertIsNone(terminal_draft(raw + "$ ", "Claude Code"))

    def test_codex_active_prompt_after_previous_turn(self):
        raw = "OpenAI Codex (v1)\n› old private turn\n• response\n› current draft\n\n  ? for shortcuts\n"
        self.assertEqual(terminal_draft(raw).text, "current draft")
        self.assertEqual(terminal_draft(raw.replace("current draft", "")).text, "")
        self.assertIsNone(terminal_draft("› ordinary shell\n  ? for shortcuts"))
        self.assertIsNone(terminal_draft("❯ sudo example\n", "Claude Code"))
        self.assertIsNone(terminal_draft("OpenAI Codex\n› old\n  ? for shortcuts\n$ echo new"))

    def test_frame_geometry_does_not_use_the_caret_or_selection(self):
        draft = Draft.plain("010 010")
        field = Snapshot((1, "x"), draft, lambda a, b: [(-100 + a, 20, -100 + b, 30)])
        spans = [{"start": n, "end": n + 3, "label": "전화"} for n in (0, 4)]
        frame = make_frame(field, spans)
        self.assertEqual(frame.hit(-95, 25).start, 4)
        self.assertIsNone(frame.hit(-97, 25))
        self.assertEqual(clip((-2, -1, 20, 5), (0, 0, 10, 10)), (0, 0, 10, 5))
        self.assertIsNone(clip((-2, -1, 0, 5), (0, 0, 10, 10)))


class LiveTests(unittest.TestCase):
    def begin(self):
        entered, release = threading.Event(), threading.Event()
        seen = []

        def preview(text):
            seen.append(text)
            if len(seen) == 1:
                entered.set()
                release.wait(2)
            return {"spans": [{"start": 0, "end": 3, "label": text[:3]}]}

        live = LiveRanges(preview)
        worker = threading.Thread(target=live.run, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(live.close)
        self.addCleanup(release.set)
        return live, seen, entered, release

    def wait_until(self, test):
        until = time.monotonic() + 2
        while time.monotonic() < until:
            if test():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for the live preview worker")

    def test_input_checks_during_continuous_typing_and_coalesces_inflight(self):
        live, seen, entered, release = self.begin()
        live.update("a", "abc ")
        self.assertTrue(entered.wait(0.5))
        for text in ("abc d", "abc de", "abc def"):
            live.update("a", text)
        release.set()
        self.wait_until(lambda: live.checked == "abc def")
        self.assertEqual(seen, ["abc ", "abc def"])
        self.assertEqual(live.update("a", "abc def!")[0]["label"], "abc")

    def test_change_tab_drops_late_draft_and_its_coordinates(self):
        live, seen, entered, release = self.begin()
        live.update((1, "a"), "old")
        self.assertTrue(entered.wait(0.5))
        live.update(None)
        live.update((1, "b"), "new")
        release.set()
        self.wait_until(lambda: live.checked == "new")
        self.assertEqual(seen, ["old", "new"])
        self.assertEqual([s["label"] for s in live.spans], ["new"])

    def test_extension_lease_is_scoped_to_its_own_draft(self):
        app = SimpleNamespace(monitoring_active=lambda: True, server_mode=lambda: True)
        actions = BrowserActions(app)
        actions.preview = Mock(return_value={"spans": []})
        actions.handle({"action": "preview", "text": "abc", "site": "chatgpt.com", "channel": "tab1"})
        self.assertTrue(actions.extension_covers("chatgpt.com", "abc"))
        self.assertFalse(actions.extension_covers("chatgpt.com", "other"))
        self.assertFalse(actions.extension_covers("cli", "abc"))
        actions.clear()
        self.assertFalse(actions.extension_covers("chatgpt.com", "abc"))

    def test_mark_click_and_outside_preserve_the_same_frame(self):
        overlay = NativeOverlay.__new__(NativeOverlay)
        overlay.pressed, overlay.press_at, overlay.chip = False, None, None
        overlay._open = Mock()
        bounds = (20, 20, 60, 40)
        frame = make_frame(
            Snapshot((7, "cli"), Draft.plain("abc"), lambda a, b: [bounds]),
            [{"start": 0, "end": 3, "label": "이름"}],
        )
        overlay._mouse(frame, 25, 25, True)
        overlay._mouse(frame, 25, 25, False)
        overlay._open.assert_called_once_with(frame, frame.marks[0], 25, 25)
        chip = Mock()
        chip.winfo_rootx.return_value = chip.winfo_rooty.return_value = 50
        chip.winfo_width.return_value = chip.winfo_height.return_value = 20
        overlay.chip = chip
        overlay._mouse(frame, 100, 100, True)
        overlay._mouse(frame, 100, 100, False)
        chip.destroy.assert_called_once()
        self.assertIsNone(overlay.chip)
        self.assertEqual(frame.hit(25, 25), frame.marks[0])
        self.assertEqual(Frame().marks, ())


@unittest.skipUnless(sys.platform in ("win32", "darwin"), "native desktop API")
class NativePlatformTests(unittest.TestCase):
    def test_translucent_panels_are_click_through_and_do_not_take_focus(self):
        if sys.platform == "win32":
            from agent.overlay import windows as platform

            before = platform.foreground()
        else:
            import AppKit as K

            from agent.overlay import macos as platform

            K.NSApplication.sharedApplication()
            before = K.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()

        paint = platform.Paint()
        try:
            paint.draw([(200, 100, 245, 120), (200, 121, 223, 141)])
            if sys.platform == "win32":
                hwnd = paint.handles[0]
                style = platform.api(
                    platform.user, "GetWindowLongW", platform.W.LONG, platform.W.HWND, platform.C.c_int
                )(hwnd, -20)
                self.assertEqual(style & 0x08080020, 0x08080020)
                self.assertEqual(platform.foreground(), before)
            else:
                self.assertTrue(paint.panels[0].ignoresMouseEvents())
                self.assertFalse(paint.panels[0].isKeyWindow())
                self.assertEqual(
                    K.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier(), before
                )
            self.assertEqual(len(paint.screen_at(210, 110)), 4)
            paint.draw([])
        finally:
            paint.close()

    @unittest.skipUnless(sys.platform == "darwin", "macOS accessibility value bridge")
    def test_accessibility_range_uses_utf16(self):
        from agent.overlay import macos as m

        offsets = m.utf16_offsets("한🧪글")
        self.assertEqual(offsets, [0, 1, 3, 4])
        extent = m.unpack(m.ax_range(offsets[2], 1), m.A.kAXValueCFRangeType)
        self.assertEqual((extent.location, extent.length), (3, 1))

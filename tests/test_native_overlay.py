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
from agent.overlay.source import (
    MARKER, Draft, Keys, Snapshot, clip, composer, retype, row_span, sends, site, terminal_draft,
)


class DraftTests(unittest.TestCase):
    def test_trusted_editors(self):
        # Claude in the browser (claude.ai, including Claude Code on the web) and Codex in the browser.
        self.assertEqual(site("https://claude.ai/new"), "claude.ai")
        self.assertEqual(site("https://claude.ai/code"), "claude.ai")
        self.assertEqual(site("https://chatgpt.com/codex"), "chatgpt.com/codex")
        self.assertEqual(site("https://chatgpt.com/codex/tasks/task_e_1?tab=diff"), "chatgpt.com/codex")
        for url in (
            "https://chatgpt.com/c/123",  # ChatGPT chats are not read, only Codex
            "https://chatgpt.com/",
            "https://chatgpt.com/codexx",
            "https://chatgpt.com.evil.test/codex",
            "http://chatgpt.com/codex",
            "https://claude.ai.evil.test",
            "https://claude.ai@evil.test",
            "http://claude.ai",
            "https://claude.ai:7777/x",
            "file:///claude.ai",
        ):
            self.assertFalse(site(url))
        self.assertTrue(composer("", "tiptap ProseMirror", "", True))
        self.assertTrue(composer("", "", "Reply to Claude", False))
        self.assertTrue(composer("prompt-textarea", "", "", False))  # ChatGPT·Codex composer
        self.assertTrue(composer("", "", "Describe a task", False))
        self.assertFalse(composer("search", "", "Search chats", True))

    def test_claude_only_draft_never_scrollback(self):
        raw = "previous reply: PRIVATE\r\n─────────────────\r\n❯ 한글 🧪 010\r\n  다음 줄\r\n─────────────────\r\n  ? for shortcuts\r\n"
        draft = terminal_draft(raw)
        self.assertEqual(draft.text, "한글 🧪 010\n다음 줄")
        self.assertEqual(draft.app, "cli:claude")
        for token in ("한글", "🧪", "010", "다음 줄"):
            offset = draft.text.index(token)
            self.assertEqual("".join(raw[a:b] for a, b in draft.parts(offset, offset + len(token))), token)
        self.assertEqual([raw[a:b] for a, b in draft.parts(5, len(draft.text))], ["010", "다음 줄"])
        self.assertEqual(raw[slice(*row_span(raw, *draft.rows))], "❯ 한글 🧪 010\r\n  다음 줄")
        self.assertIsNone(terminal_draft(raw + "$ ", "Claude Code"))

    def test_codex_active_prompt_after_previous_turn(self):
        raw = "OpenAI Codex (v1)\n› old private turn\n• response\n› current draft\n\n  ? for shortcuts\n"
        draft = terminal_draft(raw)
        self.assertEqual((draft.text, draft.app), ("current draft", "cli:codex"))
        self.assertEqual(terminal_draft(raw.replace("current draft", "")).text, "")
        self.assertIsNone(terminal_draft("› ordinary shell\n  ? for shortcuts"))
        self.assertIsNone(terminal_draft("❯ sudo example\n", "Claude Code"))
        self.assertIsNone(terminal_draft("OpenAI Codex\n› old\n  ? for shortcuts\n$ echo new"))

    def test_return_that_may_send(self):
        self.assertTrue(sends(False, False, False))   # Return
        self.assertFalse(sends(True, False, False))   # Shift+Return: a new line
        self.assertTrue(sends(False, True, False))    # Ctrl+Return
        self.assertTrue(sends(True, False, True))     # Shift+Cmd+Return

    def test_frame_geometry_does_not_use_the_caret_or_selection(self):
        draft = Draft.plain("010 010")
        field = Snapshot((1, "x"), draft, lambda a, b: [(-100 + a, 20, -100 + b, 30)])
        spans = [{"start": n, "end": n + 3, "label": "전화"} for n in (0, 4)]
        frame = make_frame(field, spans)
        self.assertEqual(frame.hit(-95, 25).start, 4)
        self.assertIsNone(frame.hit(-97, 25))
        self.assertEqual(clip((-2, -1, 20, 5), (0, 0, 10, 10)), (0, 0, 10, 5))
        self.assertIsNone(clip((-2, -1, 0, 5), (0, 0, 10, 10)))


class Prompt:
    """A TUI prompt: one caret, Backspace deletes before it, typed spaces stay but are not shown at the end."""

    def __init__(self, text, caret=None, echo=True):
        self.buffer, self.echo = text, echo
        self.caret = len(text) if caret is None else caret
        self.typed = []

    def read(self):
        return self.buffer.rstrip(" ")

    def keys(self):
        def type_(text):
            self.typed.append(text)
            if self.echo:
                self.buffer = self.buffer[: self.caret] + text + self.buffer[self.caret :]
                self.caret += len(text)
            return True

        def back(times):
            self.typed.append("\b" * times)
            for _ in range(times):
                if self.caret:
                    self.buffer = self.buffer[: self.caret - 1] + self.buffer[self.caret :]
                    self.caret -= 1
            return True

        return Keys(type_, back)


class TerminalRetypeTests(unittest.TestCase):
    def test_value_in_the_last_line_is_retyped_and_typed_spaces_stay(self):
        prompt = Prompt("고객 010-2345-6789 연락 ")
        text = prompt.read()
        a = text.index("010")
        self.assertEqual(retype(prompt.read, prompt.keys(), text, [[a, a + 13, "[전화번호_1]"]], wait=0.05),
                         "고객 [전화번호_1] 연락")
        self.assertEqual(prompt.buffer, "고객 [전화번호_1] 연락 ")
        self.assertEqual(prompt.caret, len(prompt.buffer))

    def test_caret_elsewhere_or_earlier_line_changes_nothing(self):
        prompt = Prompt("고객 010-2345-6789 연락", caret=2)
        text = prompt.read()
        self.assertIsNone(retype(prompt.read, prompt.keys(), text, [[3, 16, "[x]"]], wait=0.05))
        self.assertEqual(prompt.buffer, text)  # the marker was taken out again
        prompt = Prompt("010-2345-6789\n다음 줄")
        self.assertIsNone(retype(prompt.read, prompt.keys(), prompt.read(), [[0, 13, "[x]"]], wait=0.05))
        self.assertEqual(prompt.typed, [])  # refused before any keystroke

    def test_no_backspace_when_the_marker_never_shows(self):
        prompt = Prompt("고객 010", echo=False)
        self.assertIsNone(retype(prompt.read, prompt.keys(), prompt.read(), [[3, 6, "[x]"]], wait=0.05))
        self.assertEqual(prompt.typed, [MARKER])


class LiveTests(unittest.TestCase):
    def begin(self):
        entered, release = threading.Event(), threading.Event()
        seen = []

        def preview(text, key=None):
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
        actions.handle({"action": "preview", "text": "abc", "site": "claude.ai", "channel": "tab1"})
        self.assertTrue(actions.extension_covers("claude.ai", "abc"))
        self.assertFalse(actions.extension_covers("claude.ai", "other"))
        actions.handle({"action": "preview", "text": "xyz", "site": "chatgpt.com", "channel": "tab2"})
        self.assertFalse(actions.extension_covers("chatgpt.com", "xyz"))  # 지원하지 않는 사이트
        actions.clear()
        self.assertFalse(actions.extension_covers("claude.ai", "abc"))

    def test_mark_click_and_outside_preserve_the_same_frame(self):
        overlay = NativeOverlay.__new__(NativeOverlay)
        overlay.pressed, overlay.press_at, overlay.chip, overlay.fading = False, None, None, []
        overlay._open = Mock()
        bounds = (20, 20, 60, 40)
        frame = make_frame(
            Snapshot((7, "claude.ai"), Draft.plain("abc"), lambda a, b: [bounds]),
            [{"start": 0, "end": 3, "label": "이름"}],
        )
        overlay._mouse(frame, 25, 25, True)
        overlay._mouse(frame, 25, 25, False)
        overlay._open.assert_called_once_with(frame, frame.marks[0])
        chip = Mock()
        chip.contains.side_effect = lambda x, y: 50 <= x < 70 and 50 <= y < 70
        chip.span = (frame.key, frame.text, 0, 3)
        overlay.chip = chip
        overlay._mouse(frame, 55, 55, True)  # on the chip: the chip handles it
        overlay._mouse(frame, 55, 55, False)
        chip.press_at.assert_called_once_with(55, 55)
        chip.release_at.assert_called_once_with(55, 55)
        overlay._mouse(frame, 100, 100, True)  # outside: chips animate away
        overlay._mouse(frame, 100, 100, False)
        chip.close.assert_called_once()
        self.assertIsNone(overlay.chip)
        self.assertEqual(overlay.fading, [chip])
        self.assertEqual(frame.hit(25, 25), frame.marks[0])
        self.assertEqual(Frame().marks, ())

    def test_second_click_on_the_same_word_only_closes(self):
        overlay = NativeOverlay.__new__(NativeOverlay)
        overlay.pressed, overlay.press_at, overlay.fading = False, None, []
        overlay._open = Mock()
        frame = make_frame(
            Snapshot((7, "claude.ai"), Draft.plain("abc"), lambda a, b: [(20, 20, 60, 40)]),
            [{"start": 0, "end": 3, "label": "이름"}],
        )
        overlay.chip = Mock(span=(frame.key, frame.text, 0, 3))
        overlay.chip.contains.return_value = False
        overlay._mouse(frame, 25, 25, True)
        overlay._mouse(frame, 25, 25, False)
        overlay._open.assert_not_called()
        self.assertIsNone(overlay.chip)


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
                # Over a console the marks take the click (QuickEdit would pause the CLI), still without focus.
                paint.draw([(200, 100, 245, 120)], solid=True)
                style = platform.api(
                    platform.user, "GetWindowLongW", platform.W.LONG, platform.W.HWND, platform.C.c_int
                )(hwnd, -20)
                self.assertEqual(style & 0x08080020, 0x08080000)
                paint.draw([(200, 100, 245, 120)])
                style = platform.api(
                    platform.user, "GetWindowLongW", platform.W.LONG, platform.W.HWND, platform.C.c_int
                )(hwnd, -20)
                self.assertEqual(style & 0x08080020, 0x08080020)
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
        self.assertEqual(tuple(extent), (3, 1))

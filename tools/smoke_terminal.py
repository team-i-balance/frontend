"""The cascade chips over a CLI prompt in a real Windows console (interactive Windows desktop only).

The console runs a local fixture (this file with --tui) that draws its prompt the way the Codex CLI does:
a header, earlier output, the "›" prompt and the "? for shortcuts" footer. It reads keys one at a time like
a TUI and writes what was sent (Enter) to a file. Nothing reaches Codex, and the team server is a fixture.

Checked: only the prompt is read (never the scrollback), the word is highlighted, a typed Enter is held back
and opens the chips, clicking the word opens the cascade, 마스킹 → 적용 retypes the value in the prompt
(Korean placeholder, trailing space kept), and the masked prompt is what Enter sends.

python tools/smoke_terminal.py
"""

import ctypes
import queue
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from tempfile import TemporaryDirectory

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent))
sys.path.insert(0, str(TOOLS))
TITLE = "KP Guard fixture - Codex"
DRAFT = "sample 010-2345-6789 "
MASKED = "sample [전화번호_1] "
HEADER = ["OpenAI Codex (fixture)", "", "prior reply containing HISTORY_ONLY", ""]


def tui(sent):
    """A minimal TUI prompt: one caret, Backspace, arrows, Enter sends."""
    import msvcrt

    kernel = ctypes.windll.kernel32
    kernel.SetConsoleTitleW(TITLE)
    out = open("CONOUT$", "w", encoding="utf-8")  # also works when the runner redirects stdio
    handle = ctypes.c_void_p(msvcrt.get_osfhandle(out.fileno()))
    mode = ctypes.c_uint32()
    if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    row = len(HEADER) + 1
    out.write("\x1b[2J\x1b[H" + "\n".join(HEADER) + "\n› \n\n  ? for shortcuts\n")
    text, caret = "", 0

    def draw():
        cells = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text[:caret])
        out.write(f"\x1b[{row};1H\x1b[2K› {text}\x1b[{row};{3 + cells}H")
        out.flush()

    draw()
    while True:
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            if code == "K":
                caret = max(0, caret - 1)
            elif code == "M":
                caret = min(len(text), caret + 1)
            elif code == "G":
                caret = 0
            elif code == "O":
                caret = len(text)
        elif char == "\r":
            Path(sent).write_text(text, encoding="utf-8")
            out.write(f"\x1b[{row + 3};1H[sent]\n")
            out.flush()
            time.sleep(1)
            return
        elif char == "\x08":
            if caret:
                text, caret = text[: caret - 1] + text[caret:], caret - 1
        elif char >= " ":
            text, caret = text[:caret] + char + text[caret:], caret + 1
        draw()


def front_console(win, until):
    """Bring the fixture console to the front without clicking into it (a click starts a QuickEdit selection)."""
    window = win.u.WindowControl(searchDepth=1, RegexName=r".*KP Guard fixture - Codex.*")
    until(lambda: window.Exists(0, 0), "console window")
    hwnd = window.NativeWindowHandle
    tried = [0.0]

    def front():
        if win.foreground() == hwnd:
            return True
        if time.monotonic() - tried[0] > 0.5:
            tried[0] = time.monotonic()
            window.SetActive(waitTime=0)
        return False

    until(front, "console in front")
    time.sleep(0.4)  # the fixture draws its prompt
    return hwnd


def main():
    import tkinter as tk

    from smoke_browser import Desktop, FixtureBackend, PHONE_TEXT, SeenActions
    from smoke_native import capture, mouse

    from agent.gui import theme
    from agent.main import _dpi_aware
    from agent.overlay import windows as win
    from agent.overlay.controller import NativeOverlay

    _dpi_aware()
    root = tk.Tk()
    root.withdraw()
    theme.setup(root)
    root.update()
    posted = queue.Queue()
    with FixtureBackend() as backend, TemporaryDirectory() as folder:
        sent = Path(folder) / "sent.txt"
        app = Desktop(backend.url)
        app.root, app.settings = root, {"native_overlay": True}
        app.browser_actions = SeenActions(app)
        app.open_review = app.open_restore = app.open_files = lambda *args, **kwargs: None
        app.post = lambda command, *args: posted.put((command, args))
        overlay = NativeOverlay(app)
        console = subprocess.Popen(
            [sys.executable, __file__, "--tui", str(sent)], creationflags=subprocess.CREATE_NEW_CONSOLE
        )

        def pump():
            while True:
                try:
                    command, args = posted.get_nowait()
                except queue.Empty:
                    break
                if command == "call":
                    args[0](*args[1:])
            root.update()

        def until(predicate, what, limit=15):
            end = time.monotonic() + limit
            while time.monotonic() < end:
                pump()
                if predicate():
                    return
                time.sleep(0.01)
            chip = overlay.chip.texts() + overlay.chip.chip_labels() if overlay.chip else []
            raise AssertionError(
                f"{what} ({overlay.status}, stage={overlay.stage}, text={overlay.shown.text!r}, "
                f"marks={len(overlay.shown.marks)}, chip={chip})"
            )

        def click(point, seen):
            mouse(*point, True)
            try:
                until(seen, f"mouse down at {point}")
            finally:
                mouse(*point, False)

        def row(card, index):
            x0, y0, x1, y1 = card.row_box(index)
            return (x0 + x1) // 2, (y0 + y1) // 2

        try:
            hwnd = front_console(win, until)
            win.type_text(DRAFT)
            until(lambda: overlay.shown.marks and PHONE_TEXT in overlay.shown.text, "phone highlighted in the prompt")
            assert overlay.shown.text == DRAFT.rstrip(), overlay.shown.text
            assert all("HISTORY_ONLY" not in text for text in app.revisions), "scrollback reached the detector"
            until(lambda: overlay.target == hwnd, "Enter guarded while the prompt has a highlight")

            # A typed Enter (not injected) is held back and opens the first chip instead.
            info = win.KBDLLHOOKSTRUCT(0x0D, 0x1C, 0, 0, 0)
            assert overlay.guard is not None, "keyboard hook not installed"
            assert overlay.guard._handle(0, 0x0100, ctypes.addressof(info)) == 1, "Enter reached the CLI"
            assert overlay.guard._handle(0, 0x0101, ctypes.addressof(info)) == 1, "Enter key-up reached the CLI"
            until(lambda: overlay.chip is not None and overlay.chip.chip_labels() == ["마스킹", "원문유지"],
                  "chips after Enter")
            capture("cli-enter-held")
            overlay._dismiss()
            until(lambda: overlay.chip is None and not overlay.fading, "chips closed")

            # Click the word (the mark takes the click: no QuickEdit selection), then 마스킹 → 적용.
            mark = next(m for m in overlay.shown.marks if overlay.shown.text[m.start : m.end] == PHONE_TEXT)
            x, y, r, b = mark.bounds
            click(((x + r) // 2, (y + b) // 2), lambda: overlay.pressed)
            until(lambda: overlay.chip is not None, "chip over the word in the console")
            assert win.foreground() == hwnd, "clicking the word took focus from the console"
            click(row(overlay.chip.cards[0], 0), lambda: overlay.pressed)
            until(lambda: overlay.chip is not None and len(overlay.chip.cards) == 2
                  and overlay.chip.cards[1].menu.options[0].enabled, "masking chip")
            assert any("[전화번호_1]" in text for text in overlay.chip.texts()), overlay.chip.texts()
            capture("cli-mask")
            click(row(overlay.chip.cards[1], 0), lambda: overlay.pressed)
            until(lambda: overlay.chip is not None and "되돌리기" in overlay.chip.chip_labels(),
                  "value retyped in the CLI prompt")
            assert any("바꿨어요" in text for text in overlay.chip.texts()), overlay.chip.texts()
            until(lambda: overlay.shown.text == MASKED.rstrip() and not overlay.shown.marks, "masked prompt")
            capture("cli-masked")
            assert win.foreground() == hwnd, "the console lost the foreground"

            win.press(0x0D)  # nothing is highlighted any more: Enter goes through
            until(sent.exists, "the CLI received Enter")
            assert sent.read_text(encoding="utf-8") == MASKED, sent.read_text(encoding="utf-8")
            print("Console CLI prompt: read, highlight, Enter held, cascade chips, in-place masking and send passed")
        finally:
            overlay.close()
            if console.poll() is None:
                console.terminate()
                console.wait(timeout=10)
            root.destroy()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--tui":
        tui(sys.argv[2])
    else:
        main()

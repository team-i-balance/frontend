"""Type into a real browser window, wait for native marks, and click through them.

The two AI origins and the detector are local fixtures. No extension or browser pairing.
Run on an interactive Windows desktop, or on macOS with Accessibility granted to Python.
"""

import sys
import time
import tkinter as tk
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smoke_browser import Desktop, FixtureBackend, PHONE_TEXT, SeenActions

from agent.gui import theme
from agent.main import _dpi_aware
from agent.overlay.controller import NativeOverlay


def mouse(x, y, down):
    if sys.platform == "win32":
        from agent.overlay import windows as win

        win.user.SetCursorPos(x, y)
        win.user.mouse_event(2 if down else 4, 0, 0, 0, 0)
    else:
        import Quartz as Q

        event = Q.CGEventCreateMouseEvent(
            None, Q.kCGEventLeftMouseDown if down else Q.kCGEventLeftMouseUp, (x, y), Q.kCGMouseButtonLeft
        )
        Q.CGEventPost(Q.kCGHIDEventTap, event)


def main():
    _dpi_aware()
    root = tk.Tk()
    root.withdraw()
    theme.setup(root)
    with FixtureBackend() as backend, sync_playwright() as play:
        app = Desktop(backend.url)
        app.root, app.settings = root, {"native_overlay": True}
        app.browser_actions = SeenActions(app)
        app.open_review = app.open_restore = app.open_files = lambda *args, **kwargs: None
        app.post = lambda *args: None
        overlay = NativeOverlay(app)

        def until(predicate, description):
            limit = time.monotonic() + 12
            while time.monotonic() < limit:
                root.update()
                if predicate():
                    return
                time.sleep(0.01)
            raise AssertionError(
                f"{description} on {host} ({overlay.status}, stage={overlay.stage}, revisions={len(app.revisions)}, marks={len(overlay.shown.marks)})"
            )

        try:
            browser = play.chromium.launch(channel="chrome", headless=False)
            ctx = browser.new_context()
            documents = {
                "chatgpt.com": '<textarea id="prompt-textarea" aria-label="Message ChatGPT"></textarea>',
                "claude.ai": '<div role="textbox" aria-multiline="true" aria-label="Reply to Claude" contenteditable="true" class="tiptap ProseMirror"></div>',
                "example.org": '<textarea id="prompt-textarea"></textarea>',
            }

            def fixture(route):
                from urllib.parse import urlsplit

                host = urlsplit(route.request.url).hostname
                if host in documents and route.request.resource_type == "document":
                    content = (
                        "<title>KP Native Fixture</title><body><button>outside</button>"
                        "<style>textarea,[contenteditable] {display:block;margin:50px;width:480px;min-height:75px;font:20px sans-serif}</style>"
                        + documents[host]
                    )
                    route.fulfill(content_type="text/html", body=content)
                else:
                    route.abort()

            ctx.route("**/*", fixture)
            page = ctx.new_page()
            for host in documents:
                print(f"testing native browser: {host}", flush=True)
                page.goto(f"https://{host}/test")
                page.bring_to_front()
                field = page.locator("textarea,[contenteditable]")
                field.click()
                if sys.platform == "win32":
                    from agent.overlay import windows as win

                    # Hosted desktops can start Chromium behind the runner shell.
                    window = win.u.WindowControl(searchDepth=1, RegexName=".*KP Native Fixture.*")
                    if window.Exists(2):
                        window.SetActive(waitTime=0)
                    field.click()
                before = len(app.revisions)
                page.keyboard.type("sample ")
                if host == "example.org":
                    until(lambda: not overlay.shown.marks, "hide after navigation")
                    time.sleep(0.3)
                    assert len(app.revisions) == before, "unrelated origin reached the detector"
                    break
                until(
                    lambda: any("sample" in revision for revision in app.revisions[before:]),
                    "direct typing reached preview",
                )
                page.keyboard.type(PHONE_TEXT)
                until(
                    lambda: overlay.shown.marks and PHONE_TEXT in overlay.shown.text,
                    "native range highlighted",
                )
                mark = overlay.shown.marks[0]
                assert overlay.shown.text[mark.start : mark.end] == PHONE_TEXT
                x, y, r, b = mark.bounds
                word = (x + r) // 2, (y + b) // 2
                mouse(*word, True)
                try:
                    until(lambda: overlay.pressed, "mouse down through mark")
                finally:
                    mouse(*word, False)
                until(lambda: overlay.chip is not None, "chip above the word")
                # Click in the page margin above the editor, outside the chip.
                margin = x - 24, y - 16
                mouse(*margin, True)
                try:
                    until(lambda: overlay.chip is None, "outside click closed chip")
                finally:
                    mouse(*margin, False)
                page.get_by_role("button", name="outside").focus()
                until(lambda: not overlay.pressed, "mouse released after blur")
                until(
                    lambda: overlay.shown.hit((x + r) // 2, (y + b) // 2) is not None, "mark persists on blur"
                )
                print(f"{host}: native input, mark, chip and outside click passed")
            browser.close()
        finally:
            overlay.close()
            root.destroy()


if __name__ == "__main__":
    main()
